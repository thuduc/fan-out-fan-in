import express from 'express';
import { DEFAULTS } from './config.js';
import { RequestSubmissionService } from './requestSubmissionService.js';
import { RequestQueryService } from './requestQueryService.js';

/**
 * Creates Express app with REST API endpoints for valuation requests.
 */
export function createHttpApp({ redis, config = DEFAULTS, logger = console, curveService }) {
  if (!redis) {
    throw new Error('redis client is required');
  }

  const queryService = new RequestQueryService({ redis, logger });
  const submissionService = new RequestSubmissionService({ redis, config, logger, queryService });
  const textParser = express.text({ limit: config.curveMaxBytes || DEFAULTS.curveMaxBytes });

  const app = express();

  app.use(express.raw({
    type: (req) => {
      const contentType = req.headers['content-type'] || '';
      return contentType.includes('application/xml') || contentType.includes('text/xml');
    },
    limit: config.payloadMaxBytes || DEFAULTS.payloadMaxBytes,
  }));

  app.post('/valuation', async (req, res) => {
    try {
      const syncFlag = normalizeSyncFlag(req.query?.sync);
      if (!req.body || req.body.length === 0) {
        res.status(400).json({ message: 'XML payload is required' });
        return;
      }
      const xml = req.body.toString('utf8');
      if (!isLikelyXml(xml)) {
        res.status(400).json({ message: 'Invalid XML payload' });
        return;
      }
      const metadata = extractMetadata(req.headers);
      logger.info(`Before submissionService.submit syncFlag=${syncFlag}`);
      const result = await submissionService.submit({ xml, sync: syncFlag === 'Y', metadata });
      logger.info(`After submissionService.submit syncFlag=${syncFlag}`);
      if (syncFlag === 'Y') {
        if (result.status === 'completed') {
          res.type('application/xml').status(200).send(result.responseXml || '');
        } else if (result.status === 'failed') {
          const failure = await queryService.getFailure(result.requestId);
          res.status(500).json({
            message: 'Processing failed',
            requestId: result.requestId,
            detail: failure || null,
          });
        } else {
          res.status(202).json({ requestId: result.requestId, status: 'pending' });
        }
        return;
      }
      res.status(202).json({ requestId: result.requestId, status: 'accepted' });
    } catch (error) {
      logger.error?.('Failed to handle POST /valuation', { error });
      res.status(error.statusCode || 500).json({ message: error.message || 'Internal Server Error' });
    }
  });

  app.get('/valuation/:requestId/status', async (req, res) => {
    try {
      const { requestId } = req.params;
      const status = await queryService.getStatus(requestId);
      if (!status) {
        res.status(404).json({ message: 'Request not found' });
        return;
      }
      res.json(formatStatus(status));
    } catch (error) {
      logger.error?.('Failed to handle GET status', { error });
      res.status(500).json({ message: 'Internal Server Error' });
    }
  });

  app.get('/valuation/:requestId/results', async (req, res) => {
    try {
      const { requestId } = req.params;
      const xml = await queryService.getResult(requestId);
      if (xml) {
        res.type('application/xml').send(xml);
        return;
      }
      const status = await queryService.getStatus(requestId);
      if (!status) {
        res.status(404).json({ message: 'Request not found' });
        return;
      }
      if (status.status && status.status.startsWith('failed')) {
        const failure = await queryService.getFailure(requestId);
        res.status(422).json({ requestId, status: status.status, detail: failure || null });
        return;
      }
      res.status(404).json({ message: 'Result not yet available', status: status.status || 'pending' });
    } catch (error) {
      logger.error?.('Failed to handle GET results', { error });
      res.status(500).json({ message: 'Internal Server Error' });
    }
  });

  app.get('/healthz', async (_req, res) => {
    try {
      await redis.ping?.();
      res.json({ status: 'ok' });
    } catch (error) {
      res.status(500).json({ status: 'error', message: error.message });
    }
  });

  app.post('/curveBuildingRequest', async (req, res, next) => {
    const finish = async () => {
      if (!curveService) {
        throw Object.assign(new Error('Curve building service unavailable'), { statusCode: 500 });
      }

      let payload = req.curvePayload;
      if (!payload && req.file) {
        payload = {
          buffer: req.file.buffer,
          contentType: req.file.mimetype || 'text/plain',
          originalName: req.file.originalname || 'curve.mkt',
        };
      }

      if (!payload) {
        throw Object.assign(new Error('Curve payload is required'), { statusCode: 400 });
      }
      if (!payload.buffer || payload.buffer.length === 0) {
        throw Object.assign(new Error('Curve payload must not be empty'), { statusCode: 400 });
      }
      const maxBytes = config.curveMaxBytes || DEFAULTS.curveMaxBytes;
      if (payload.buffer.length > maxBytes) {
        throw Object.assign(new Error('Curve payload exceeds allowed size'), { statusCode: 413 });
      }

      const result = await curveService.submitCurveRequest(payload);
      res.set('requestStatus', 'success');
      res.set('Content-Type', 'text/plain');
      const downloadOptions = {
        headers: {
          'Content-Type': 'text/plain',
        },
      };
      res.download(result.outputPath, 'finalResult.xml', downloadOptions, async (err) => {
        await result.cleanup?.().catch(() => {});
        if (err && !res.headersSent) {
          logger.error?.('Failed to stream curve result', { error: err });
          res.status(500).json({ message: 'Failed to stream result' });
        } else if (err) {
          logger.error?.('Failed to stream curve result', { error: err });
        }
      });
    };

    try {
      const contentType = req.headers['content-type'] || '';
      if (contentType.includes('text/plain')) {
        textParser(req, res, async (err) => {
          if (err) {
            next(err);
            return;
          }
          req.curvePayload = {
            buffer: Buffer.from(req.body ?? '', 'utf8'),
            contentType: 'text/plain',
            originalName: 'curve.mkt',
          };
          try {
            await finish();
          } catch (error) {
            next(error);
          }
        });
        return;
      }

      if (contentType.startsWith('multipart/form-data')) {
        try {
          const payload = await parseMultipartForm(req, config.curveMaxBytes || DEFAULTS.curveMaxBytes);
          req.curvePayload = payload;
          await finish();
        } catch (error) {
          next(error);
        }
        return;
      }

      throw Object.assign(new Error('Unsupported Content-Type'), { statusCode: 415 });
    } catch (error) {
      next(error);
    }
  }, (error, req, res, _next) => {
    logger.error?.('Failed to handle POST /curveBuildingRequest', { error });
    const status = error.statusCode || 500;
    if (res.headersSent) {
      return;
    }
    res.status(status).json({ message: error.message || 'Internal Server Error' });
  });

  return app;
}

/**
 * Normalizes sync query parameter to 'Y' or 'N'.
 */
function normalizeSyncFlag(value) {
  if (!value) {
    return 'N';
  }
  const upper = String(value).trim().toUpperCase();
  if (upper === 'Y') {
    return 'Y';
  }
  if (upper === 'N') {
    return 'N';
  }
  throw Object.assign(new Error('sync must be Y or N'), { statusCode: 400 });
}

/**
 * Extracts metadata from request headers (x-* headers).
 */
function extractMetadata(headers) {
  const metadata = {};
  for (const [key, value] of Object.entries(headers || {})) {
    if (!key.startsWith('x-')) {
      continue;
    }
    metadata[key] = value;
  }
  return metadata;
}

/**
 * Formats internal state object for API response.
 */
function formatStatus(state) {
  const response = {
    requestId: state.requestId,
    status: state.status || 'unknown',
  };
  if (typeof state.groupCount === 'number') {
    response.groupCount = state.groupCount;
  }
  if (typeof state.currentGroup === 'number') {
    response.currentGroup = state.currentGroup;
  }
  if (state.receivedAt) {
    response.receivedAt = state.receivedAt;
  }
  if (state.completedAt) {
    response.completedAt = state.completedAt;
  }
  return response;
}

/**
 * Basic XML validation (checks for angle brackets).
 */
function isLikelyXml(xml) {
  const trimmed = xml.trim();
  return trimmed.startsWith('<') && trimmed.endsWith('>');
}

async function parseMultipartForm(req, maxBytes) {
  const contentType = req.headers['content-type'] || '';
  const boundaryMatch = contentType.match(/boundary=(?:"?)([^";]+)(?:"?)/i);
  if (!boundaryMatch) {
    throw Object.assign(new Error('multipart boundary missing'), { statusCode: 400 });
  }
  const boundary = boundaryMatch[1];
  const rawBody = await readRequestBody(req, maxBytes);
  const delimiter = `--${boundary}`;
  const parts = rawBody.toString('binary').split(delimiter);
  for (const part of parts) {
    const trimmed = part.trim();
    if (!trimmed || trimmed === '--') {
      continue;
    }
    const [headerSection, ...bodySections] = trimmed.split('\r\n\r\n');
    if (!headerSection || bodySections.length === 0) {
      continue;
    }
    const bodyBinary = bodySections.join('\r\n\r\n');
    const headers = headerSection.split('\r\n').reduce((acc, line) => {
      const [name, value] = line.split(':');
      if (name && value !== undefined) {
        acc[name.trim().toLowerCase()] = value.trim();
      }
      return acc;
    }, {});

    const disposition = headers['content-disposition'] || '';
    if (!/form-data/i.test(disposition)) {
      continue;
    }
    const filenameMatch = disposition.match(/filename="([^"]*)"/i);
    const originalName = filenameMatch ? filenameMatch[1] : 'curve.mkt';
    const partContentType = headers['content-type'] || 'text/plain';

    // Remove trailing boundary markers
    const cleanBody = bodyBinary.replace(/\r\n--$/, '').replace(/\r\n$/, '');
    const buffer = Buffer.from(cleanBody, 'binary');
    return {
      buffer,
      contentType: partContentType,
      originalName,
    };
  }

  throw Object.assign(new Error('No file part found'), { statusCode: 400 });
}

function readRequestBody(req, maxBytes) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    let total = 0;
    req.on('data', (chunk) => {
      total += chunk.length;
      if (total > maxBytes) {
        reject(Object.assign(new Error('Curve payload exceeds allowed size'), { statusCode: 413 }));
        req.destroy();
        return;
      }
      chunks.push(chunk);
    });
    req.on('end', () => {
      resolve(Buffer.concat(chunks));
    });
    req.on('error', (error) => reject(error));
  });
}
