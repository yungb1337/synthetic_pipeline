export const meta = {
  name: 'audit-modules',
  description: 'Autonomous audit of the existing modules (architecture + quality + duplication), fix flagged issues, re-review until PASS, then checkpoint. Fire-and-forget counterpart to /dev-team.',
  phases: [
    { title: 'Audit', detail: 'architecture + quality reviewers scan the modules in parallel' },
    { title: 'Fix', detail: 'implementation engineer addresses flagged issues' },
    { title: 'Re-review', detail: 'reviewers re-check; loop until both PASS' },
    { title: 'Checkpoint', detail: 'knowledge curator writes ADRs, status, final report' },
  ],
}

const RUN_ID = 'run-2026-08-04-audit'
const RUN_DIR = `checkpoints/run/${RUN_ID}`
const PYTEST = ".venv/Scripts/python.exe -m pytest tests/ -q"
const SIM_CHECK = "python scripts/check_similarity.py"

const REVIEW_SCHEMA = {
  type: 'object',
  properties: {
    verdict: { type: 'string', enum: ['PASS', 'FAIL'] },
    issues: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          severity: { type: 'string', enum: ['blocking', 'major', 'minor'] },
          file: { type: 'string' },
          summary: { type: 'string' },
        },
        required: ['severity', 'summary'],
      },
    },
  },
  required: ['verdict', 'issues'],
}

const READ_PROTOCOL = 'Read docs/org-gate-protocol.md and CLAUDE.md first.'

phase('Audit')
log(`Auditing modules -> ${RUN_DIR}`)

const audit = await parallel([
  () => agent(
    'You are the Architecture Reviewer. ' + READ_PROTOCOL + ' Audit app/parser, app/normalizer, ' +
    'app/processing, app/embedding for alignment with the specs in docs/ and the ADRs in ' +
    'project_memory/architecture_decisions.md. Check coupling, interfaces, boundaries, and ' +
    'future-proofing. Write ' + RUN_DIR + '/reviews/architecture.md ending with VERDICT: PASS or FAIL.',
    { label: 'architecture-review', phase: 'Audit', schema: REVIEW_SCHEMA }
  ),
  () => agent(
    'You are the Quality & Performance Reviewer. ' + READ_PROTOCOL + ' Run the similarity checker ' +
    '(' + SIM_CHECK + ') and review app/ for duplication, testability, ' +
    'maintainability, and scale/performance concerns. Run ' + PYTEST + ' ' +
    '(informational). Write ' + RUN_DIR + '/reviews/quality.md ending with VERDICT: PASS or FAIL.',
    { label: 'quality-review', phase: 'Audit', schema: REVIEW_SCHEMA }
  ),
])

const allIssues = audit.filter(Boolean).flatMap((r) => r.issues)
log(`Audit found ${allIssues.length} issue(s) across both reviewers`)

if (allIssues.length > 0) {
  phase('Fix')
  await agent(
    'You are the Implementation Engineer. ' + READ_PROTOCOL + ' Fix the following audit issues, keeping ' +
    'modular-monolith boundaries and matching existing style. Write tests where behavior changes. ' +
    'Keep the suite green: run ' + PYTEST + '. ' +
    'Write ' + RUN_DIR + '/engineer-report.md. Issues:\n' + JSON.stringify(allIssues, null, 2),
    { label: 'engineer-fix', phase: 'Fix' }
  )
  log('Engineer finished; starting re-review loop')
} else {
  log('No issues found - skipping the fix phase')
}

phase('Re-review')
let passed = false
for (let round = 1; round <= 3 && !passed; round++) {
  const re = await parallel([
    () => agent(
      'You are the Architecture Reviewer. Re-audit app/ after the previous fix round (round ' + round + '). ' +
      'Write ' + RUN_DIR + '/reviews/architecture-round' + round + '.md ending with VERDICT: PASS or FAIL.',
      { label: `re-arch-${round}`, phase: 'Re-review', schema: REVIEW_SCHEMA }
    ),
    () => agent(
      'You are the Quality & Performance Reviewer. Re-audit app/ after the previous fix round ' +
      '(round ' + round + '), rerunning ' + SIM_CHECK + '. Write ' +
      RUN_DIR + '/reviews/quality-round' + round + '.md ending with VERDICT: PASS or FAIL.',
      { label: `re-qual-${round}`, phase: 'Re-review', schema: REVIEW_SCHEMA }
    ),
  ])
  const fails = re.filter(Boolean).filter((r) => r.verdict === 'FAIL')
  if (fails.length === 0) {
    passed = true
    log(`Re-review round ${round}: PASS`)
    break
  }
  const issues = fails.flatMap((r) => r.issues)
  log(`Round ${round}: still ${issues.length} issue(s) -> engineer fixes`)
  await agent(
    'You are the Implementation Engineer. Fix round ' + (round + 1) + ' issues. Keep the suite green: ' +
    PYTEST + '. Issues:\n' + JSON.stringify(issues, null, 2),
    { label: `engineer-fix-${round + 1}`, phase: 'Re-review' }
  )
}

phase('Checkpoint')
const summary = await agent(
  'You are the Knowledge Curator. Read everything under ' + RUN_DIR + '/ and the current ' +
  'project_memory/. Update project_memory/module_status.md, project_memory/architecture_decisions.md, ' +
  'project_memory/questions.md, and the MEMORY.md index. Write ' + RUN_DIR + '/checkpoint.md and ' +
  RUN_DIR + '/final-report.md (one page: what was audited, what was fixed, reviewer verdicts, ' +
  'test status). Respect append-only. Return a one-line summary of the run.',
  { label: 'knowledge-curator', phase: 'Checkpoint' }
)

log(`Run finished: ${summary}`)
return {
  runId: RUN_ID,
  finalReport: `${RUN_DIR}/final-report.md`,
  allReviewersPassed: passed,
  auditIssueCount: allIssues.length,
}