import {
  Controller,
  Get,
  HttpException,
  HttpStatus,
  Inject,
  Param,
  ParseUUIDPipe,
  Query,
} from '@nestjs/common';
import { CadAgentRepository } from './cad-agent.repository';
import { WorkflowError } from './contracts';
import { publicJob } from './public-job';

function httpError(error: unknown): HttpException {
  if (error instanceof WorkflowError) {
    const status =
      error.code === 'INVALID_REQUEST' || error.code === 'INVALID_PART_TYPE'
        ? HttpStatus.BAD_REQUEST
        : error.code.endsWith('_NOT_FOUND') || error.code === 'EDIT_JOB_MISSING'
          ? HttpStatus.NOT_FOUND
          : error.code === 'CLIENT_REQUEST_ID_CONFLICT'
            ? HttpStatus.CONFLICT
            : HttpStatus.UNPROCESSABLE_ENTITY;
    return new HttpException({ code: error.code, message: error.message }, status);
  }
  return new HttpException(
    { code: 'INTERNAL_ERROR', message: 'The CAD agent could not process the request.' },
    HttpStatus.INTERNAL_SERVER_ERROR,
  );
}

@Controller('v1/cad-edits')
export class CadEditsController {
  constructor(
    @Inject(CadAgentRepository) private readonly repository: CadAgentRepository,
  ) {}

  @Get(':jobId')
  async status(
    @Param('jobId', new ParseUUIDPipe()) jobId: string,
    @Query('after_sequence') rawAfterSequence?: string,
  ) {
    const afterSequence = Number(rawAfterSequence ?? 0);
    if (!Number.isSafeInteger(afterSequence) || afterSequence < 0) {
      throw new HttpException(
        { code: 'INVALID_SEQUENCE', message: 'after_sequence must be a non-negative integer.' },
        HttpStatus.BAD_REQUEST,
      );
    }
    try {
      const job = await this.repository.editJob(jobId);
      const events = await this.repository.events(jobId, afterSequence);
      return { job: publicJob(job as unknown as Record<string, unknown>), events };
    } catch (error) {
      throw httpError(error);
    }
  }
}

export { publicJob } from './public-job';
