import json, time, uuid, base64, pathlib
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization

ISS = "http://kc.audit.local/realms/bullm"; AUD = "sbam-api"
KEYFILE = pathlib.Path("/tmp/audit-poc/live/key.pem")
def key():
    if KEYFILE.exists():
        return serialization.load_pem_private_key(KEYFILE.read_bytes(), None)
    k = rsa.generate_private_key(65537, 2048)
    KEYFILE.write_bytes(k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return k
def jwks():
    from jwt.algorithms import RSAAlgorithm
    j = json.loads(RSAAlgorithm.to_jwk(key().public_key())); j.update(kid="k1", use="sig", alg="RS256")
    return {"keys": [j]}
def token(sub, tenant, roles, *, name="u", email=None, ttl=300, aud=AUD, iss=ISS, kid="k1", alg="RS256", k=None, extra=None):
    now = int(time.time())
    claims = {"iss": iss, "aud": aud, "sub": str(sub), "iat": now, "exp": now + ttl,
              "realm_access": {"roles": roles}, "preferred_username": name,
              "email": email or f"{name}@audit.test", "sid": str(uuid.uuid4())}
    if tenant: claims["tenant_id"] = [str(tenant)]
    claims.update(extra or {})
    return jwt.encode(claims, k or key(), algorithm=alg, headers={"kid": kid})
