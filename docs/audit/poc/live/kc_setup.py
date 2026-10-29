import httpx, json, subprocess
KC="http://localhost:58080"; 
adm=httpx.post(KC+"/realms/master/protocol/openid-connect/token",data={"grant_type":"password","client_id":"admin-cli","username":"admin","password":"audit-admin-pw"}).json()["access_token"]; A={"Authorization":"Bearer "+adm}; api=lambda p: KC+"/admin/realms/bullm"+p
def sql(q): return subprocess.run(["docker","exec","audit-pg","psql","-U","sbam","-d","sbam","-At","-c",q],capture_output=True,text=True).stdout.strip()
TA=sql("select id from sbam.tenants where name='TenantA'")
# redirect URIs for the audit Next server on :3001
for cid in ("sbam-fde","sbam-admin"):
    c=[x for x in httpx.get(api("/clients?clientId="+cid),headers=A).json()][0]
    uris=set(c["redirectUris"])|{f"http://localhost:3001/api/auth/callback/keycloak-{cid.split('-')[1]}"}; origins=set(c.get("webOrigins",[]))|{"http://localhost:3001"}
    r=httpx.put(api("/clients/"+c["id"]),headers=A,json={**c,"redirectUris":sorted(uris),"webOrigins":sorted(origins)}); print(cid, r.status_code)
org=httpx.post(api("/organizations"),headers=A,json={"name":"TenantA","alias":TA,"domains":[{"name":"audit.test"}]}); print("org", org.status_code, org.text[:120])
oid=[o for o in httpx.get(api("/organizations"),headers=A).json() if o["alias"]==TA][0]["id"]
def mk(name, role):
    r=httpx.post(api("/users"),headers=A,json={"username":name,"email":f"{name}@audit.test","emailVerified":True,"firstName":"K","lastName":name,"enabled":True,"credentials":[{"type":"password","value":"audit-User-Strong-Pw-9!","temporary":False}]})
    uid=httpx.get(api("/users?username="+name+"&exact=true"),headers=A).json()[0]["id"]
    httpx.post(api(f"/users/{uid}/role-mappings/realm"),headers=A,json=[httpx.get(api("/roles/"+role),headers=A).json()])
    m=httpx.post(api(f"/organizations/{oid}/members"),headers={**A,"content-type":"application/json"},content=json.dumps(uid)); print(name, role, "org member", m.status_code); return uid
ids={"kcadmin":mk("kcadmin","sbam-admin"),"kcfde":mk("kcfde","sbam-fde")}; json.dump({"tenant":TA,"org":oid,**ids}, open("/tmp/audit-poc/live/kc_users.json","w")); print(ids)
