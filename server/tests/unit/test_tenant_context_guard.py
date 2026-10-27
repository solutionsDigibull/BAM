"""E15.6b: a project-owned repository is never built with a tenant-only TenantContext.

`TenantScopedRepository._scoped` adds the project filter only when `ctx.project_id` is set, so
`DocumentVersionRepository(session, TenantContext(tenant))` silently reads every project of the
tenant (#340). This AST scan fails on that pattern. Tenant-owned repositories (projects, members,
notifications, masters) legitimately use a tenant-only context and stay allowed.
"""

import ast
import importlib
import pkgutil
from pathlib import Path

import sbam_api
import sbam_workers
from sbam_api.db.base import ProjectOwned
from sbam_api.db.repository import TenantScopedRepository

# (relative path, repository class) pairs reviewed as safe. Empty today; keep it tiny and justified.
ALLOWLIST: set[tuple[str, str]] = set()

_ROOTS = [Path(p) for pkg in (sbam_api, sbam_workers) for p in pkg.__path__]


def _import_all() -> None:
    for pkg in (sbam_api, sbam_workers):
        for mod in pkgutil.walk_packages(pkg.__path__, pkg.__name__ + "."):
            try:
                importlib.import_module(mod.name)
            except Exception:  # noqa: S112 - optional deps; a missing module hides nothing
                continue


def _project_owned_repos() -> set[str]:
    _import_all()
    found: set[str] = set()
    todo = list(TenantScopedRepository.__subclasses__())
    while todo:
        cls = todo.pop()
        todo.extend(cls.__subclasses__())
        model = getattr(cls, "model", None)
        if isinstance(model, type) and issubclass(model, ProjectOwned):
            found.add(cls.__name__)
    return found


def _is_tenant_only(call: ast.AST) -> bool:
    return (
        isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
        and call.func.id == "TenantContext"
        and len(call.args) + len(call.keywords) == 1
        and all(k.arg != "project_id" for k in call.keywords)
    )


def violations(source: str, project_repos: set[str], path: str = "<src>") -> list[str]:
    """`path:line Repo` for each project-owned repository given a tenant-only context."""
    tree = ast.parse(source)
    out: list[str] = []
    scopes: list[ast.AST] = [tree] + [
        n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
    ]
    for scope in scopes:
        tenant_only: set[str] = set()
        for node in ast.walk(scope):
            if isinstance(node, ast.Assign) and _is_tenant_only(node.value):
                tenant_only.update(t.id for t in node.targets if isinstance(t, ast.Name))
        for node in ast.walk(scope):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
                continue
            if node.func.id not in project_repos or len(node.args) < 2:
                continue
            ctx = node.args[1]
            if _is_tenant_only(ctx) or (isinstance(ctx, ast.Name) and ctx.id in tenant_only):
                if (path, node.func.id) not in ALLOWLIST:
                    out.append(f"{path}:{node.lineno} {node.func.id}")
    return sorted(set(out))


def test_e15_6b_t01_no_project_owned_repo_uses_a_tenant_only_context() -> None:
    repos = _project_owned_repos()
    assert "DocumentVersionRepository" in repos  # the scan really sees project-owned repos
    found: list[str] = []
    for root in _ROOTS:
        for py in root.rglob("*.py"):
            found += violations(py.read_text(), repos, str(py.relative_to(root.parent)))
    assert not found, "tenant-only TenantContext on a project-owned repository: " + ", ".join(found)


def test_e15_6b_t02_catches_a_reintroduced_tenant_only_lookup() -> None:
    repos = _project_owned_repos()
    inline = "def f(s, t):\n    return DocumentVersionRepository(s, TenantContext(t)).get(1)\n"
    via_name = (
        "def f(s, t):\n    ctx = TenantContext(t)\n    return DocumentVersionRepository(s, ctx)\n"
    )
    assert violations(inline, repos) == ["<src>:2 DocumentVersionRepository"]
    assert violations(via_name, repos) == ["<src>:3 DocumentVersionRepository"]


def test_e15_6b_t03_project_scoped_and_tenant_owned_uses_pass() -> None:
    repos = _project_owned_repos()
    ok = (
        "def f(s, t, p):\n"
        "    DocumentVersionRepository(s, TenantContext(t, p))\n"
        "    DocumentVersionRepository(s, TenantContext(t, project_id=p))\n"
        "    ProjectRepository(s, TenantContext(t))\n"
    )
    assert violations(ok, repos) == []
