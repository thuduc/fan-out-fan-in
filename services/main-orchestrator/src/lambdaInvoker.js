import { INVOCATION_TIMEOUT_MS } from './constants.js';

/**
 * Thin wrapper around an AWS Lambda client to allow dependency injection in tests.
 */
export class LambdaInvoker {
  constructor({ client, functionName, commandFactory, logger }) {
    if (!client) {
      throw new Error('client is required');
    }
    if (!functionName) {
      throw new Error('functionName is required');
    }
    if (typeof commandFactory !== 'function') {
      throw new Error('commandFactory must be provided');
    }
    this.client = client;
    this.functionName = functionName;
    this.commandFactory = commandFactory;
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
    return this.commandFactory({
      FunctionName: this.functionName,
      InvocationType: 'Event',
      Payload: Buffer.from(JSON.stringify(payload))
    });
  }
}
