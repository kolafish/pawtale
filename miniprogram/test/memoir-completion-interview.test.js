require("./register-ts");
const assert = require("node:assert/strict");
const test = require("node:test");
const { mockModule, clearModule } = require("./page-harness");

for (const available of [true,false]) test(`offered completion rechecks availability before binding: ${available}`, async () => {
  const requests=[], routes=[];
  const navigate = options => { routes.push(options.url); options.success(); };
  global.wx={navigateTo:navigate,redirectTo:navigate};
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

test("navigation failure resumes the bound supplement instead of creating another session", async () => {
  let issue = { id: "i1", channel: "interview", status: "open" };
  let created = 0;
  const routes = [];
  global.wx = { redirectTo: options => {
    routes.push(options.url);
    if (routes.length === 1) options.fail({ errMsg: "redirectTo:fail" });
    else options.success();
  } };
  mockModule("../services/memoir-completion-issue-api.ts", { listMemoirCompletionIssues: async () => ({ issues: [issue] }) });
  mockModule("../services/memoir-voice-session-api.ts", { createMemoirVoiceSession: async () => {
    created += 1;
    issue = { ...issue, status: "in_progress", active_session_id: "bound-session" };
    return { session: { id: "bound-session" } };
  } });
  mockModule("../services/audit-tracker.ts", { trackAuditAction() {} });
  mockModule("../services/voice-authorization.ts", { readVoiceAuthorizationConsent: () => true, syncVoiceAuthorizationConsent: async () => {} });
  clearModule("../services/memoir-completion-interview.ts");
  const { openOfferedCompletionInterview } = require("../services/memoir-completion-interview.ts");
  const open = () => openOfferedCompletionInterview({ projectId: "p1", projectTitle: "回忆录", issueId: "i1", replacePage: true });
  await assert.rejects(open, /未能打开/);
  await open();
  assert.equal(created, 1);
  assert.equal(routes.length, 2);
  assert.equal(routes[0], routes[1]);
  assert.match(routes[1], /voiceSessionId=bound-session/);
});

test("authorization replaces a stopped voice page before a supplement is created", async () => {
  const routes = [];
  global.wx = { redirectTo: options => { routes.push(options.url); options.success(); } };
  mockModule("../services/memoir-completion-issue-api.ts", { listMemoirCompletionIssues: async () => ({ issues: [{ id: "i1", channel: "interview", status: "open" }] }) });
  mockModule("../services/memoir-voice-session-api.ts", { createMemoirVoiceSession: async () => { assert.fail("no session before consent"); } });
  mockModule("../services/voice-authorization.ts", { readVoiceAuthorizationConsent: () => false, VOICE_AUTHORIZATION_PAGE_URL: "/pages/voice-authorization/index" });
  clearModule("../services/memoir-completion-interview.ts");
  await require("../services/memoir-completion-interview.ts").openOfferedCompletionInterview({ projectId: "p1", projectTitle: "回忆录", issueId: "i1", replacePage: true });
  assert.deepEqual(routes, ["/pages/voice-authorization/index"]);
});
