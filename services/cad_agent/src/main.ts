import 'reflect-metadata';
import { Logger } from '@nestjs/common';
import { NestFactory } from '@nestjs/core';
import { WsAdapter } from '@nestjs/platform-ws';
import { AppModule } from './app.module';

async function bootstrap() {
  const app = await NestFactory.create(AppModule);
  app.useWebSocketAdapter(new WsAdapter(app));
  app.enableShutdownHooks();
  const port = Number(process.env.CAD_AGENT_PORT ?? 3000);
  if (!Number.isSafeInteger(port) || port < 1 || port > 65_535) {
    throw new Error('CAD_AGENT_PORT must be an integer between 1 and 65535.');
  }
  const host = process.env.CAD_AGENT_HOST?.trim() || '0.0.0.0';
  await app.listen(port, host);
  Logger.log(`CAD agent HTTP/WebSocket service listening on ${host}:${port}`);
}

void bootstrap();
