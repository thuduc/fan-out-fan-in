/**
 * Handles synchronous curve building requests by forwarding payloads to Lambda.
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
   * Submits curve payload to downstream Lambda and returns response buffer.
   */
  async submitCurveRequest({ buffer, contentType }) {
    const payload = {
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

    let body = response.bodyBase64
      ? Buffer.from(response.bodyBase64, 'base64').toString('utf8')
      : response.body ?? '';

    return {
      statusCode: response.statusCode ?? 200,
      body,
      contentType: response.contentType || 'text/plain',
    };
  }
}
