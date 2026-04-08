---
name: code-quality-guide
description: 'Python/Vue 3 code quality standards for the EMR/STP stack. Covers project-specific conventions, HIPAA constraints, schema-per-tenant patterns, and patterns models commonly get wrong. Do NOT use for VZ service_desk.'
---

# Code Quality Guide — EMR/STP

## What This Covers

Project-specific conventions and patterns models get wrong for the EMR stack: FastAPI + Vue 3 (Composition API + Pinia) + PostgreSQL with schema-per-tenant isolation, targeting Azure HIPAA-compliant deployment.

---

## 1. Pydantic Trust Model — Model-First Defensive Coding

Same rule as VZ: **trust validated Pydantic models downstream, validate hard at boundaries.**

The test: *"Can I construct a valid scenario where this branch triggers?"* If no — delete it.

```python
# BAD — required field cannot be None post-validation
if client.date_of_birth is not None:
    calculate_age(client.date_of_birth)

# GOOD
calculate_age(client.date_of_birth)
```

Defensive code IS appropriate for:
- External API responses before validation
- `Optional` fields explicitly marked `str | None`
- `Union` types requiring narrowing
- API entry points and file I/O

---

## 2. Pydantic Model Naming Standards

| Suffix | Purpose | Example |
|--------|---------|---------|
| `*RequestModel` | API input / GET `Depends()` / POST body | `ClientCreateRequestModel` |
| `*ResponseModel` | API output / serialized response | `ClientResponseModel` |
| `*SchemaModel` | SQLAlchemy-backed schema | `SessionNoteSchemaModel` |

**GET endpoints always use `Depends()` — never path variables for query params:**

```python
@router.get("/api/v1/clients")
async def get_clients(query: ClientSearchRequestModel = Depends()):
    ...
```

---

## 3. Route Return Pattern — Explicit Model Construction

Always construct and return the response model explicitly — never return a plain dict:

```python
# GOOD
return ClientResponseModel(**client_dict)

# BAD
return client_dict
return {"id": client_id, "name": name}
```

---

## 4. Schema-Per-Tenant Pattern

Every database operation must be scoped to the tenant schema. Never query the public schema for tenant data.

```python
# GOOD — always set search_path before queries
async def get_db(tenant_id: str) -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        await session.execute(text(f"SET search_path TO tenant_{tenant_id}"))
        yield session

# BAD — queries public schema, returns all tenants' data
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session
```

Tenant ID must be derived from the authenticated JWT — never from a request parameter.

---

## 5. HIPAA Data Handling

### PHI Fields

The following are Protected Health Information (PHI) and must never appear in logs, error messages, or non-encrypted storage:

- `date_of_birth`
- `ssn` / `tax_id`
- `diagnosis_codes`
- `session_notes` (free text)
- `medications`
- `insurance_id`
- Any field containing client name + date combination

### Logging — Never Log PHI

```python
# GOOD — log IDs and events, never content
logger.info(f"Session note created: [session_id={session_id}] [client_id={client_id}]")

# BAD — PHI in logs
logger.info(f"Note for {client.full_name}: {note.content}")
```

### Error Messages — Never Expose PHI

```python
# GOOD
raise HTTPException(404, detail=f"Client not found: [{client_id}]")

# BAD
raise HTTPException(404, detail=f"Client {client.full_name} (DOB: {client.dob}) not found")
```

---

## 6. Public/Private Function Separator

Use a three-line comment block to separate public from private functions:

```python
async def create_session_note(note_in: SessionNoteRequestModel, db: AsyncSession) -> SessionNoteResponseModel:
    """Public service function."""
    note = await _validate_and_create(note_in, db)
    return SessionNoteResponseModel.model_validate(note)


#
# Private
#


async def _validate_and_create(note_in: SessionNoteRequestModel, db: AsyncSession):
    """Private helper — validates business rules and persists."""
    pass
```

---

## 7. Error Handling Conventions

Wrap variables in `[]` in f-strings for debug isolation:

```python
except Exception as exc:
    raise HTTPException(
        status_code=400,
        detail=dict(error=f"Failed to create session note [{session_id}]: {exc}"),
    ) from exc
```

Never include PHI in exception detail strings.

---

## 8. Documentation Standards

### Docstrings — Mandatory on Every Function Definition

Every function and method must have a docstring — no exceptions, including private helpers and short functions.

```python
# GOOD
async def _validate_and_create(note_in: SessionNoteRequestModel, db: AsyncSession):
    """Validate business rules and persist session note. PHI fields are never logged here."""
    pass

# BAD — no docstring
async def _validate_and_create(note_in: SessionNoteRequestModel, db: AsyncSession):
    pass
```

### Business Context Comments — Required at Non-Obvious Call Sites

Add a one-line comment explaining *why* at call sites where the reason isn't self-evident from the function name. Do not comment obvious sequential calls.

```python
# GOOD — explains why, not what
# Set tenant search_path before any query — schema-per-tenant isolation requires this on every session
await session.execute(text(f"SET search_path TO tenant_{tenant_id}"))

# Verify cross-tenant access is blocked — HIPAA requires strict tenant boundary enforcement
assert await _get_client(other_tenant_db, client_id) is None

# BAD — restates the function name
# Get the client
client = await get_client(client_id, db)
# Create the note
note = await create_note(note_in, db)
```

**When a comment IS required:**
- Tenant isolation enforcement
- PHI boundary enforcement
- Async coordination or locking
- Business rule enforcement not obvious from the name
- Any call a reviewer would ask "why here?"

## 9. Vue 3 — Composition API + Pinia

### Always use `<script setup>` — not Options API

```vue
<!-- GOOD -->
<script setup lang="ts">
import { ref, computed } from 'vue'
import { useClientStore } from '@/stores/client'

const store = useClientStore()
const clients = computed(() => store.activeClients)
</script>

<!-- BAD — Options API is not used in this project -->
<script>
export default {
  data() { return { clients: [] } }
}
</script>
```

### State management is Pinia — not Vuex

```typescript
// GOOD — Pinia store
export const useClientStore = defineStore('client', () => {
  const clients = ref<Client[]>([])
  const loading = ref(false)

  async function fetchClients() {
    loading.value = true
    try {
      clients.value = await api.getClients()
    } finally {
      loading.value = false
    }
  }

  return { clients, loading, fetchClients }
})
```

### Component file length limit: 300 lines max

Split at 300 — extract sub-components.

---

## 9. Secrets and Configuration

Use Azure Key Vault via environment injection — never hardcode credentials:

```python
# GOOD — injected by Azure at runtime
DATABASE_URL = os.environ["DATABASE_URL"]  # set by Azure Key Vault reference

# BAD
DATABASE_URL = "postgresql://user:password@host/db"
```

---

## 10. Testing Standards

- **Minimum coverage**: 80% overall, 90% for auth/PHI handling/tenant isolation
- **Tenant isolation tests are mandatory** — every data access test must verify cross-tenant data cannot be accessed
- **Naming**: `test_<method>_<scenario>`, class `Test<ClassName>`, file `test_<module>.py`
- Use SQLite in-memory for unit tests, real PostgreSQL for integration tests
- Never use production or staging PHI data in tests — use synthetic data only

---

## 11. Quality Gates

- Ruff linting — must pass
- mypy — must pass  
- 80% test coverage minimum
- No hardcoded secrets (gitleaks)
- No PHI in logs or error messages
- All public API endpoints documented
- Tenant isolation verified in tests

---

## TODO — Decisions Pending

- Logging framework: structlog vs standard logging (TBD with Patrick)
- SQLAlchemy async patterns: repository layer design (TBD)
- Azure Key Vault integration pattern (TBD)
- Session note encryption at rest strategy (TBD)
