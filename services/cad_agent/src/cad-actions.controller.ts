import {
  Body,
  Controller,
  HttpCode,
  HttpException,
  HttpStatus,
  Inject,
  Logger,
  Post,
} from '@nestjs/common';
import { CadActionsService } from './cad-actions.service';
import { ActionError, WorkflowError } from './contracts';

function workflowStatus(code: string): number {
  if (code.endsWith('_NOT_FOUND') || code === 'EDIT_JOB_MISSING') return HttpStatus.NOT_FOUND;
  if (code.endsWith('_EXISTS') || code === 'CLIENT_REQUEST_ID_CONFLICT') return HttpStatus.CONFLICT;
  if (code === 'INVALID_REQUEST' || code === 'INVALID_PART_TYPE') return HttpStatus.BAD_REQUEST;
  return HttpStatus.INTERNAL_SERVER_ERROR;
}

@Controller('v1/cad-agent/actions')
export class CadActionsController {
  private readonly logger = new Logger(CadActionsController.name);

  constructor(@Inject(CadActionsService) private readonly actions: CadActionsService) {}

  @Post()
  @HttpCode(HttpStatus.OK)
  async execute(@Body() body: unknown): Promise<Record<string, unknown>> {
    try {
      return await this.actions.execute(body);
    } catch (error) {
      if (error instanceof ActionError) {
        throw new HttpException({ error: error.message }, error.status);
      }
      if (error instanceof WorkflowError) {
        throw new HttpException({ error: error.message }, workflowStatus(error.code));
      }
      this.logger.error(error instanceof Error ? error.stack : String(error));
      throw new HttpException(
        { error: 'The CAD agent could not process the request.' },
        HttpStatus.INTERNAL_SERVER_ERROR,
      );
    }
  }
}
