import time

from bidops.company.authorization import CompanyAuthorization, CompanyError
from bidops.company.materials import MaterialStore
from bidops.company.repository import CompanyRepository
from bidops.screening.schemas import VersionRef


class CompanyService:
    def __init__(
        self, db, data_dir, *, max_bytes=2_000_000, max_text_chars=200_000, clock=time.time
    ):
        self.db = db
        self.auth = CompanyAuthorization(db, clock)
        self.materials = MaterialStore(
            db, data_dir, self.auth, max_bytes=max_bytes, max_text_chars=max_text_chars
        )
        self.repo = CompanyRepository(db, self.auth, self.materials)

    def backup_files(self):
        return self.materials.backup_files()

    def require_inputs(self, context, versions):
        """Recheck fixed inputs before tool execution AND before releasing its result.

        The caller retains only owner identity and immutable VersionRefs, never an
        authorization verdict. This is the contract for future tasks; those task
        entry points must call it again when their execution/release is implemented.
        """
        checked = []
        for value in versions:
            version = VersionRef.model_validate(value)
            obj = self.auth.require(context, version.object_id, "read")
            if obj["kind"] == "profile":
                record = self.repo.get(context, version.object_id, version.version_id)
                checked.extend(
                    ref["version"]["object_id"]
                    for fact in record["payload"]["facts"]
                    for ref in fact["evidence"]
                )
            else:
                record, _, _ = self.materials.read(context, version.object_id, version.version_id)
            if record["sha256"] != version.sha256:
                raise CompanyError("company_input_version_mismatch", 409)
            checked.append(version.object_id)
        self.auth.require_many(context, checked)
