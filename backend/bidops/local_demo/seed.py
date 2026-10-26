"""Fictional company materials through normal ACLs, with private restartable references."""

import json
import os
import re
import secrets
import threading

from bidops.company.authorization import CompanyError, ToolContext

KIND = "local_workflow"
LABEL = "本机完整流程演示"
DESCRIPTION = (
    "公告来自已导入的历史真实原文；企业与材料是虚构工程示例，不能作为真实企业凭证。"
    "资格判断保留未知、缺件及过期资料状态，须由人逐项复核。"
)
COMPANY_NAME = "虚构示例企业 · 北辰环境技术演示公司（非真实企业）"
MATERIALS = (
    (
        "虚构企业情况说明",
        "虚构示例企业情况说明\n企业名称：北辰环境技术演示公司（非真实企业）。\n"
        "业务方向：环境监测技术服务。服务区域：天津。\n"
        "本文件只用于本机工程演示，不是营业执照、审计报告、资质证书或官方证明。",
    ),
    (
        "虚构资料缺件清单",
        "虚构示例资料缺件清单\n尚未提供：营业执照、财务审计材料、依法缴税和社保证明、"
        "专项资质、项目业绩与人员证书。\n"
        "企业规模及中小企业声明状态未知。未提交联合体、分包及利益冲突说明。\n"
        "不得把缺失材料解释为符合资格，也不得把本清单当作官方企业凭证。",
    ),
)


class WorkflowSeed:
    def __init__(self, company, data_dir):
        self.company = company
        self.root = data_dir / "private" / "local-demo"
        self.lock = threading.Lock()

    def _path(self, owner_id):
        if not owner_id.isalnum() or len(owner_id) != 64:
            raise CompanyError("local_demo_identity_invalid", 409)
        # Never follow a user-created link out of private demonstration storage.
        if any(path.is_symlink() for path in [self.root, *self.root.parents]):
            raise CompanyError("local_demo_manifest_path_invalid", 409)
        path = self.root / (owner_id + ".json")
        if path.is_symlink():
            raise CompanyError("local_demo_manifest_path_invalid", 409)
        return path

    def _load(self, owner_id):
        path = self._path(owner_id)
        if not path.exists():
            return {
                "version": 1,
                "owner_id": owner_id,
                "seed_key": secrets.token_hex(16),
                "materials": [],
            }
        try:
            if path.stat().st_size > 30_000:
                raise ValueError("manifest too large")
            value = json.loads(path.read_text())
            if value["version"] != 1 or value["owner_id"] != owner_id:
                raise ValueError("manifest ownership")
            if not re.fullmatch(r"[a-f0-9]{32}", value["seed_key"]):
                raise ValueError("manifest identity")
            if not isinstance(value["materials"], list) or len(value["materials"]) > 2:
                raise ValueError("manifest materials")
            for ref in [
                *value["materials"],
                *([value["profile_revision"]] if "profile_revision" in value else []),
            ]:
                if not all(
                    isinstance(ref[key], str) and ref[key]
                    for key in ("object_id", "version_id", "sha256")
                ):
                    raise ValueError("manifest reference")
            if any(
                not isinstance(ref.get("label"), str) or not ref["label"]
                for ref in value["materials"]
            ):
                raise ValueError("manifest material label")
            if "profile_revision" in value and len(value["materials"]) != 2:
                raise ValueError("incomplete seed")
            return value
        except (OSError, ValueError, KeyError, TypeError):
            raise CompanyError("local_demo_manifest_invalid", 409) from None

    def _save(self, value):
        path = self._path(value["owner_id"])
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.chmod(0o700)
        temporary = self.root / (".seed-" + secrets.token_hex(16))
        try:
            fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "w") as stream:
                json.dump(value, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def _verify(self, context, value):
        for material in value["materials"]:
            record, _, _ = self.company.materials.read(
                context, material["object_id"], material["version_id"]
            )
            if record["sha256"] != material["sha256"]:
                raise CompanyError("local_demo_seed_changed", 409)
        if "profile_revision" in value:
            ref = value["profile_revision"]
            profile = self.company.repo.get(context, ref["object_id"], ref["version_id"])
            if profile["sha256"] != ref["sha256"]:
                raise CompanyError("local_demo_seed_changed", 409)

    def _response(self, owner_id, value):
        response = {"kind": KIND, "label": LABEL, "description": DESCRIPTION}
        response["seeded"] = "profile_revision" in value
        if response["seeded"]:
            response.update(
                profile_revision=value["profile_revision"],
                additional_material_revisions=[
                    {key: item[key] for key in ("object_id", "version_id", "sha256")}
                    for item in value["materials"]
                ],
                reviewer_subject_id=owner_id,
                company_name=COMPANY_NAME,
                materials=value["materials"],
            )
        return response

    def _recover(self, context, *, marker, profile=False):
        # A crash can occur after the normal service commits but before the file
        # checkpoint. Recover only our durable random marker, never a display name.
        table = "company_profile_revisions" if profile else "company_material_revisions"
        predicate = (
            "json_extract(payload_json,'$.source_attestation')" if profile else "source_attestation"
        )
        found = self.company.db.rows(
            f"SELECT object_id,version_id,sha256 FROM {table} "
            f"WHERE created_by=:owner AND {predicate}=:marker",
            {"owner": context.owner_id, "marker": marker},
        )
        if len(found) > 1:
            raise CompanyError("local_demo_seed_recovery_ambiguous", 409)
        if not found:
            return None
        ref = found[0]
        if profile:
            self.company.repo.get(context, ref["object_id"], ref["version_id"])
        else:
            self.company.materials.read(context, ref["object_id"], ref["version_id"])
        return ref

    def get(self, owner_id):
        with self.lock:
            value = self._load(owner_id)
            self._verify(ToolContext(owner_id), value)
            return self._response(owner_id, value)

    def create(self, owner_id):
        with self.lock:
            context = ToolContext(owner_id)
            self.company.auth.require_creator(context)
            value = self._load(owner_id)
            self._verify(context, value)
            # Persist intent before the first business write, enabling recovery
            # if an object commit succeeds but its reference checkpoint fails.
            self._save(value)
            for index in range(len(value["materials"]), len(MATERIALS)):
                label, body = MATERIALS[index]
                marker = f"local-demo:{value['seed_key']}:material-{index}"
                ref = self._recover(context, marker=marker) or self.company.materials.put(
                    context,
                    filename=label + "（仅工程演示）.txt",
                    content_type="text/plain",
                    data=body.encode(),
                    provenance_kind="engineering_fixture",
                    source_attestation=marker,
                )
                value["materials"].append(
                    {
                        "label": label,
                        **{key: ref[key] for key in ("object_id", "version_id", "sha256")},
                    }
                )
                self._save(value)
            if "profile_revision" not in value:
                marker = f"local-demo:{value['seed_key']}:profile"
                recovered = self._recover(context, marker=marker, profile=True)
                if recovered:
                    value["profile_revision"] = recovered
                    self._save(value)
                    return self._response(owner_id, value)
                evidence = []
                for material in value["materials"]:
                    _, _, body = self.company.materials.read(
                        context, material["object_id"], material["version_id"]
                    )
                    evidence.append(
                        self.company.materials.citation(
                            context, material["object_id"], material["version_id"], 0, len(body)
                        )
                    )
                value["profile_revision"] = self.company.repo.put(
                    context,
                    {
                        "display_name": COMPANY_NAME,
                        "provenance_kind": "engineering_fixture",
                        "source_attestation": marker,
                        "facts": [
                            {
                                "fact_id": "demo-industry",
                                "kind": "industry",
                                "label": "演示业务方向（虚构自述）",
                                "value": "环境监测技术服务",
                                "status": "self_reported",
                            },
                            {
                                "fact_id": "demo-region",
                                "kind": "region",
                                "label": "演示服务区域（虚构自述）",
                                "value": "天津",
                                "status": "self_reported",
                            },
                            {
                                "fact_id": "demo-missing-qualifications",
                                "kind": "qualification",
                                "label": "资格证明尚未提供",
                                "status": "unknown",
                                "reason": "虚构工程示例尚无真实企业证明，须补齐材料并人工复核。",
                                "evidence": evidence,
                            },
                        ],
                    },
                )
                self._save(value)
            return self._response(owner_id, value)
