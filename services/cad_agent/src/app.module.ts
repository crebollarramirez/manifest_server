import { Module } from '@nestjs/common';
import { CadAgentRepository } from './cad-agent.repository';
import { CadEditsController } from './cad-edits.controller';
import { CadEditsGateway } from './cad-edits.gateway';
import { OrchestratorService } from './orchestrator.service';
import { ProgressService } from './progress.service';
import { ReasonerService } from './reasoner.service';
import { SubmissionService } from './submission.service';

@Module({
  controllers: [CadEditsController],
  providers: [
    CadAgentRepository,
    SubmissionService,
    ProgressService,
    ReasonerService,
    OrchestratorService,
    CadEditsGateway,
  ],
})
export class AppModule {}
