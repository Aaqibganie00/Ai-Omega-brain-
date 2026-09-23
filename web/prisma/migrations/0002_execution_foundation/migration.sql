ALTER TABLE "Project" ADD COLUMN IF NOT EXISTS "phase3_placeholder" BOOLEAN NOT NULL DEFAULT FALSE;

CREATE TYPE "AgentRunStatus" AS ENUM ('queued', 'running', 'completed', 'failed', 'blocked');
CREATE TYPE "ExecutionStatus" AS ENUM ('queued', 'running', 'succeeded', 'failed', 'timed_out', 'rejected');

CREATE TABLE "AgentRun" (
  "id" TEXT NOT NULL,
  "projectId" TEXT NOT NULL,
  "taskId" TEXT,
  "agentId" TEXT NOT NULL,
  "role" TEXT NOT NULL,
  "status" "AgentRunStatus" NOT NULL DEFAULT 'queued',
  "input" JSONB,
  "output" JSONB,
  "error" JSONB,
  "metadata" JSONB,
  "startedAt" TIMESTAMP(3),
  "completedAt" TIMESTAMP(3),
  "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "updatedAt" TIMESTAMP(3) NOT NULL,
  CONSTRAINT "AgentRun_pkey" PRIMARY KEY ("id")
);
CREATE TABLE "ExecutionRecord" (
  "id" TEXT NOT NULL,
  "agentRunId" TEXT,
  "projectId" TEXT NOT NULL,
  "taskId" TEXT,
  "kind" TEXT NOT NULL,
  "status" "ExecutionStatus" NOT NULL DEFAULT 'queued',
  "command" TEXT,
  "stdout" TEXT,
  "stderr" TEXT,
  "exitCode" INTEGER,
  "durationMs" INTEGER,
  "error" JSONB,
  "metadata" JSONB,
  "startedAt" TIMESTAMP(3),
  "completedAt" TIMESTAMP(3),
  "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "updatedAt" TIMESTAMP(3) NOT NULL,
  CONSTRAINT "ExecutionRecord_pkey" PRIMARY KEY ("id")
);
CREATE INDEX "AgentRun_projectId_createdAt_idx" ON "AgentRun"("projectId", "createdAt");
CREATE INDEX "AgentRun_taskId_idx" ON "AgentRun"("taskId");
CREATE INDEX "ExecutionRecord_projectId_createdAt_idx" ON "ExecutionRecord"("projectId", "createdAt");
CREATE INDEX "ExecutionRecord_agentRunId_idx" ON "ExecutionRecord"("agentRunId");
ALTER TABLE "AgentRun" ADD CONSTRAINT "AgentRun_projectId_fkey" FOREIGN KEY ("projectId") REFERENCES "Project"("id") ON DELETE CASCADE ON UPDATE CASCADE;
ALTER TABLE "AgentRun" ADD CONSTRAINT "AgentRun_taskId_fkey" FOREIGN KEY ("taskId") REFERENCES "Task"("id") ON DELETE SET NULL ON UPDATE CASCADE;
ALTER TABLE "ExecutionRecord" ADD CONSTRAINT "ExecutionRecord_agentRunId_fkey" FOREIGN KEY ("agentRunId") REFERENCES "AgentRun"("id") ON DELETE SET NULL ON UPDATE CASCADE;
ALTER TABLE "ExecutionRecord" ADD CONSTRAINT "ExecutionRecord_projectId_fkey" FOREIGN KEY ("projectId") REFERENCES "Project"("id") ON DELETE CASCADE ON UPDATE CASCADE;
ALTER TABLE "ExecutionRecord" ADD CONSTRAINT "ExecutionRecord_taskId_fkey" FOREIGN KEY ("taskId") REFERENCES "Task"("id") ON DELETE SET NULL ON UPDATE CASCADE;
