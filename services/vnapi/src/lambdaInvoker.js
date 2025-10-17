import { INVOCATION_TIMEOUT_MS } from './constants.js';
import { LambdaClient, InvokeCommand } from '@aws-sdk/client-lambda';

/**
 * Wrapper around AWS Lambda client for invoking vnvs orchestrator.
 * Provides async invocation with timeout and error handling.
 */
export class LambdaInvoker {
  constructor({ functionName, logger, client, defaultInvocationType = 'Event' } = {}) {
    if (!functionName) {
      throw new Error('functionName is required');
    }

    const region = process.env.AWS_REGION || 'us-east-1';

    this.client = client || new LambdaClient({ region });
    this.functionName = functionName;
    this.logger = logger || console;
    this.defaultInvocationType = defaultInvocationType;
  }

  /**
   * Invokes Lambda asynchronously (fire-and-forget).
   */
  async invokeAsync(payload, { functionName = this.functionName, invocationType = 'Event', timeoutMs = INVOCATION_TIMEOUT_MS } = {}) {
    return this._invoke(payload, { functionName, invocationType, timeoutMs, expectResponse: false });
  }

  /**
   * Invokes Lambda synchronously and returns parsed response.
   */
  async invokeSync(payload, { functionName = this.functionName, timeoutMs = 15000 } = {}) {
    return this._invoke(payload, {
      functionName,
      invocationType: 'RequestResponse',
      timeoutMs,
      expectResponse: true,
    });
  }

  /**
   * Shared invocation logic.
   */
  async _invoke(payload, { functionName, invocationType, timeoutMs, expectResponse }) {
    const command = new InvokeCommand({
      FunctionName: functionName,
      InvocationType: invocationType || this.defaultInvocationType,
      Payload: Buffer.from(JSON.stringify(payload)),
    });
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const response = await this.client.send(command, { abortSignal: controller.signal });
      this.logger.debug?.('Lambda invocation succeeded', { functionName, invocationType });
      if (!expectResponse) {
        return response;
      }

      const payloadBuffer = response.Payload ? Buffer.from(response.Payload) : Buffer.alloc(0);
      let parsed = {};
      if (payloadBuffer.length > 0) {
        try {
          parsed = JSON.parse(payloadBuffer.toString('utf8'));
        } catch {
          parsed = {
            bodyBase64: payloadBuffer.toString('base64'),
          };
        }
      }
      const bodyBase64 = parsed.bodyBase64;
      const bodyString = parsed.body;
      const body = bodyBase64
        ? Buffer.from(bodyBase64, 'base64')
        : bodyString !== undefined
          ? Buffer.from(bodyString, 'utf8')
          : Buffer.alloc(0);
      return {
        body,
        contentType: parsed.contentType || 'text/plain',
        functionError: response.FunctionError,
        errorMessage: parsed.errorMessage || parsed.message,
      };
    } catch (error) {
      this.logger.error?.('Lambda invocation failed', { functionName, error });
      throw error;
    } finally {
      clearTimeout(timeout);
    }
  }
}
