import { Module } from '@nestjs/common';
import { CadAgentRepository } from './cad-agent.repository';
import { CadActionsController } from './cad-actions.controller';
import { CadActionsService } from './cad-actions.service';
import { CadEditsController } from './cad-edits.controller';
import { CadEditsGateway } from './cad-edits.gateway';
import { OrchestratorService } from './orchestrator.service';
import { ProgressService } from './progress.service';
import { ReasonerService } from './reasoner.service';
import { MeshGenerationService } from './mesh-generation.service';
import { SubmissionService } from './submission.service';

@Module({
  controllers: [CadEditsController, CadActionsController],
  providers: [
    CadAgentRepository,
    SubmissionService,
    CadActionsService,
    MeshGenerationService,
    ProgressService,
    ReasonerService,
    OrchestratorService,
    CadEditsGateway,
  ],
})
export class AppModule {}
