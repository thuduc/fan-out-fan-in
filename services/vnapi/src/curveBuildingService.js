import { writeFile, mkdtemp, rm } from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';

/**
 * Handles synchronous curve building requests by forwarding payloads to Lambda.
 * Persists request/response artifacts to temporary files for auditing.
 */
export class CurveBuildingService {
  constructor({ lambdaInvoker, config, logger } = {}) {
    if (!lambdaInvoker) {
      throw new Error('lambdaInvoker is required');
    }
    this.lambdaInvoker = lambdaInvoker;
    this.config = config || {};
    this.logger = logger || console;
  }

  /**
   * Submits curve payload to downstream Lambda and returns file paths for response.
   */
  async submitCurveRequest({ buffer, contentType, originalName = 'curve.mkt' }) {
    const tmpBase = await mkdtemp(path.join(os.tmpdir(), 'curve-'));
    const inputPath = path.join(tmpBase, 'input.mkt');
    const outputPath = path.join(tmpBase, 'finalResult.xml');

    try {
      await writeFile(inputPath, buffer);
      const payload = {
        filename: originalName,
        contentType,
        bodyBase64: buffer.toString('base64'),
      };
      const response = await this.lambdaInvoker.invokeSync(payload, {
        functionName: this.config.curveLambdaName,
        timeoutMs: this.config.curveTimeoutMs,
      });

      if (response.functionError) {
        const error = new Error(response.errorMessage || 'Curve Lambda failed');
        error.statusCode = 502;
        throw error;
      }

      const bodyBuffer = response.body ?? Buffer.alloc(0);
      await writeFile(outputPath, bodyBuffer);

      return {
        inputPath,
        outputPath,
        contentType: response.contentType || 'text/plain',
        cleanup: () => rm(tmpBase, { recursive: true, force: true }),
      };
    } catch (error) {
      await rm(tmpBase, { recursive: true, force: true }).catch(() => {});
      throw error;
    }
  }
}
