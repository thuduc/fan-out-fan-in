import { LambdaClient, InvokeCommand } from "@aws-sdk/client-lambda";
import { INVOCATION_TIMEOUT_MS } from './constants.js';
import { LambdaClient, InvokeCommand } from '@aws-sdk/client-lambda';

/**
 * Thin wrapper around an AWS Lambda client to allow dependency injection in tests.
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

  _buildInvokeCommand(payload) {
    return new InvokeCommand({
      FunctionName: this.functionName,
      Payload: Buffer.from(JSON.stringify(payload))
    });
  }
}
