require("./register-ts");
const assert = require("node:assert/strict");
const test = require("node:test");
const { mockModule, clearModule } = require("./page-harness");

for (const available of [true,false]) test(`offered completion rechecks availability before binding: ${available}`, async () => {
  const requests=[], routes=[];
  global.wx={navigateTo:options=>routes.push(options.url),redirectTo:options=>routes.push(options.url)};
  mockModule("../services/memoir-completion-issue-api.ts",{listMemoirCompletionIssues:async()=>({issues:available?[{id:"i1",channel:"interview",status:"open"}]:[]})});
  mockModule("../services/memoir-voice-session-api.ts",{createMemoirVoiceSession:async(body,options)=>{requests.push({body,options});return {session:{id:"new-session"}};}});
  mockModule("../services/audit-tracker.ts",{trackAuditAction(){}});
  mockModule("../services/voice-authorization.ts",{readVoiceAuthorizationConsent:()=>true,syncVoiceAuthorizationConsent:async()=>{}});
  clearModule("../services/memoir-completion-interview.ts");
  const {openOfferedCompletionInterview}=require("../services/memoir-completion-interview.ts");
  const action=()=>openOfferedCompletionInterview({projectId:"p1",projectTitle:"回忆录",issueId:"i1",replacePage:true});
  if (!available) { await assert.rejects(action,/已经处理/);assert.deepEqual(requests,[]);assert.deepEqual(routes,[]); }
  else {
    await action();
    assert.equal(requests[0].body.session_goal,"book_completion");
    assert.equal(requests[0].body.current_completion_issue_id,"i1");
    assert.equal(requests[0].options.projectId,"p1");
    assert.equal(routes.length,1);
    assert.match(routes[0],/voiceSessionId=new-session/);
  }
});
