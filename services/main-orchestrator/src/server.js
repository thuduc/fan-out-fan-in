import Redis from 'ioredis';
import { DEFAULTS } from './config.js';
import { createHttpApp } from './httpApp.js';
import { MainOrchestrator } from './mainOrchestrator.js';
import { RequestStateRepository } from './requestStateRepository.js';
import { LifecyclePublisher } from './lifecyclePublisher.js';
import { LambdaInvoker } from './lambdaInvoker.js';

async function bootstrap() {
  const logger = console;
  const redisUrl = process.env.REDIS_URL || 'redis://127.0.0.1:6379';
  const redis = new Redis(redisUrl, { lazyConnect: false });
  redis.on('error', (error) => logger.error('Redis connection error', { error }));

  const stateRepository = new RequestStateRepository(redis);
  const lifecyclePublisher = new LifecyclePublisher(redis);
  const requestInvoker = new LambdaInvoker({
    client: { send: async () => {} },
    functionName: process.env.REQUEST_ORCHESTRATOR_ARN || 'request-orchestrator-stub',
    commandFactory: (payload) => payload,
    logger,
  });

  const orchestrator = new MainOrchestrator({
    redis,
    stateRepository,
    lifecyclePublisher,
    requestInvoker,
    logger,
  });

  orchestrator.ensureConsumerGroup().catch((error) => logger.error('Consumer group init failed', error));
  orchestrator.startPolling().catch((error) => logger.error('Polling loop stopped', error));

  const app = createHttpApp({ redis, config: DEFAULTS, logger });
  const port = DEFAULTS.httpPort;
  app.listen(port, () => {
    logger.info(`HTTP server listening on port ${port}`);
  });

  const shutdown = async () => {
    logger.info('Shutting down server');
    orchestrator.stop();
    try {
      await redis.quit();
    } catch (error) {
      logger.warn?.('Error quitting redis', { error });
    }
    process.exit(0);
  };

  process.on('SIGINT', shutdown);
  process.on('SIGTERM', shutdown);
}

bootstrap().catch((error) => {
  console.error('Failed to start server', error);
  process.exitCode = 1;
});
