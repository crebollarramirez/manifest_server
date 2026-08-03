import 'reflect-metadata';
import { Logger } from '@nestjs/common';
import { NestFactory } from '@nestjs/core';
import { WsAdapter } from '@nestjs/platform-ws';
import { AppModule } from './app.module';

async function bootstrap() {
  const app = await NestFactory.create(AppModule);
  app.useWebSocketAdapter(new WsAdapter(app));
  app.enableShutdownHooks();
  const port = Number(3000);
  const host = process.env.CAD_AGENT_HOST?.trim() || '0.0.0.0';
  await app.listen(port, host);
  Logger.log(`CAD agent HTTP/WebSocket service listening on ${host}:${port}`);
}

void bootstrap();
