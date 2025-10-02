import express from 'express';
import { DEFAULTS } from './config.js';
import { RequestSubmissionService } from './requestSubmissionService.js';
import { RequestQueryService } from './requestQueryService.js';

/**
 * Creates Express app with REST API endpoints for valuation requests.
 */
export function createHttpApp({ redis, config = DEFAULTS, logger = console }) {
  if (!redis) {
    throw new Error('redis client is required');
  }

  const queryService = new RequestQueryService({ redis, logger });
  const submissionService = new RequestSubmissionService({ redis, config, logger, queryService });

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
