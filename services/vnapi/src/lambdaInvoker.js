import { INVOCATION_TIMEOUT_MS } from './constants.js';
import { LambdaClient, InvokeCommand } from '@aws-sdk/client-lambda';

/**
 * Wrapper around AWS Lambda client for invoking vnvs orchestrator.
 * Provides async invocation with timeout and error handling.
 */
export class LambdaInvoker {
  constructor({ functionName, logger }) {
    if (!functionName) {
      throw new Error('functionName is required');
    }

    const region = process.env.AWS_REGION || "us-east-1";

    this.client = new LambdaClient({ region });
    this.functionName = functionName;
    this.logger = logger || console;
  }

  /**
   * Invokes vnvs Lambda asynchronously with timeout.
   */
  async invokeAsync(payload) {
    const command = this._buildInvokeCommand(payload);
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), INVOCATION_TIMEOUT_MS);
    try {
      await this.client.send(command, { abortSignal: controller.signal });
      this.logger.debug?.('Lambda invocation succeeded', { functionName: this.functionName });
    } catch (error) {
      this.logger.error?.('Lambda invocation failed', { functionName: this.functionName, error });
      throw error;
    } finally {
      clearTimeout(timeout);
    }
  }

  /**
   * Builds AWS Lambda InvokeCommand from payload.
   */
  _buildInvokeCommand(payload) {
    return new InvokeCommand({
      FunctionName: this.functionName,
      Payload: Buffer.from(JSON.stringify(payload))
    });
  }
}
