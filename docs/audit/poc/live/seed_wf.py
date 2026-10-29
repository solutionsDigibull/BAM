"""Seed workflows/macros/sub-skills/code attempts for both tenants straight into the throwaway DB (FKs relaxed)."""
import sys, json, uuid, hashlib, subprocess
sys.path.insert(0, "/home/Fool/Arch - Work/BAM-audit/server/tests")
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sbam_api.core.ids import uuid7
from sbam_api.workflows.models import Workflow
from sbam_api.isatvon.models import MacroSkill, SubSkill
from sbam_api.builds.models import CodeAttempt
from sbam_api.sow.models import Sow, SowVersion
from sbam_api.db.base import utcnow
sample = json.load(open("/home/Fool/Arch - Work/BAM-audit/server/tests/fixtures/isa14/ms01_1_structured.json"))
def q(s): return subprocess.run(["docker","exec","audit-pg","psql","-U","sbam","-d","sbam","-At","-c",s],capture_output=True,text=True).stdout.strip()
eng = create_engine("postgresql+psycopg://postgres:auditroot@127.0.0.1:55999/sbam")
out = {}
with Session(eng) as s:
    s.execute(text("SET session_replication_role = replica"))
    for label, tname, pname in (("A","TenantA","ProjA"),("B","TenantB","ProjB")):
        tid = uuid.UUID(q(f"select id from sbam.tenants where name='{tname}'")); pid = uuid.UUID(q(f"select id from sbam.projects where tenant_id='{tid}' and name='{pname}'"))
        sow = Sow(id=uuid7(), tenant_id=tid, project_id=pid); s.add(sow)
        sv = SowVersion(id=uuid7(), tenant_id=tid, project_id=pid, sow_id=sow.id, version=1, source="GENERATED", run_id=uuid7(), knowledge_version_id=uuid7(), state="APPROVED", structured=None, sha256="0"*64); s.add(sv); sow.approved_version_id = sv.id; sow.current_version_id = sv.id
        wf = Workflow(id=uuid7(), tenant_id=tid, project_id=pid, sow_version_id=sv.id, major=1, version="1.0.0", name=f"wf-{label}", state="ISATVON_REVIEW", created_by="SYSTEM"); s.add(wf)
        macros = []
        for mi in (1, 2):
            m = MacroSkill(id=uuid7(), tenant_id=tid, project_id=pid, workflow_id=wf.id, sow_version_id=sv.id, macro_category=f"MS0{mi}_Test{mi}", name=f"Macro {mi}", description="d", order=mi, sow_refs=["m1"], state="PENDING"); s.add(m); macros.append(m)
        subs = []
        for mi, m in enumerate(macros, 1):
            for si in (1, 2, 3):
                body = json.loads(json.dumps(sample)); body["skill_metadata"]["skill_name"] = f"Sub {mi}.{si}"
                sub = SubSkill(id=uuid7(), tenant_id=tid, project_id=pid, macro_skill_id=m.id, workflow_id=wf.id, skill_id=f"MS0{mi}.{si}_Sub", skill_name=f"Sub {mi}.{si}", order=si, isatvon=body, sow_refs=["m1"], content_sha256=hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest(), state="VALID"); s.add(sub); subs.append(sub)
        att = CodeAttempt(id=uuid7(), tenant_id=tid, project_id=pid, workflow_id=wf.id, sub_skill_id=subs[0].id, build_no=1, cycle=0, attempt=0, template_name="t", template_version="1", template_sha256="0"*64, status="BODIES_GENERATED", bodies={"action_body":"pass","check_body":"pass","state_queries":{},"helpers":""}, llm_call_ids=[], trace_id="t", findings=[]); s.add(att)
        out[label] = dict(tenant=str(tid), project=str(pid), workflow=str(wf.id), macros=[str(m.id) for m in macros], subs=[str(x.id) for x in subs])
    s.commit()
json.dump(out, open("/tmp/audit-poc/live/seed.json","w"), indent=1); print(json.dumps({k:{kk:(vv if kk in ('workflow',) else '...') for kk,vv in v.items()} for k,v in out.items()}))
