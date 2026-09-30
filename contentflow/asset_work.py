"""Version-bound asset work across unlocked provider and storage calls.

Read checks avoid unnecessary I/O; only the final locked check authorizes a
business write. Superseded results roll back at the Worker transaction boundary,
so an already uploaded object remains a charged, recoverable staging record.
"""

from dataclasses import dataclass
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from .asset_operations import lock_asset_for_mutation
from .entities import Asset, ContentItem
from .execution_fence import fence_domain_write


ASSET_WORK_SESSION_KEY = "contentflow_asset_work"


def _identity(asset: Asset) -> tuple:
    # Selecting a different sibling must not cancel generation or be overwritten
    # by its old metadata. All other request/source fields remain version-bound.
    metadata = {key: value for key, value in (asset.metadata_json or {}).items()
        if key != "selected"}
    return (
        asset.content_item_id, asset.content_version, asset.kind, asset.provider,
        asset.prompt, asset.external_task_id, asset.storage_uri, asset.status,
        json.dumps(metadata, sort_keys=True, separators=(",", ":")),
    )


@dataclass(frozen=True)
class AssetWork:
    asset_id: str
    workspace_id: str
    identity: tuple

    @classmethod
    def begin(cls, session: Session, asset: Asset) -> "AssetWork":
        work = cls(asset.id, asset.workspace_id, _identity(asset))
        session.info[ASSET_WORK_SESSION_KEY] = work
        return work

    def require_current(self, session: Session, *, lock: bool = False) -> Asset:
        # In particular, do not flush the pending storage activation before
        # checking that the object can still be attached to this version.
        with session.no_autoflush:
            if lock:
                # Global order: execution Job -> parent content -> asset.
                fence_domain_write(session)
                asset = lock_asset_for_mutation(session, self.workspace_id, self.asset_id)
            else:
                asset = session.scalar(select(Asset).where(
                    Asset.id == self.asset_id, Asset.workspace_id == self.workspace_id,
                ).execution_options(populate_existing=True))
            if asset is None or asset.status == "stale" or _identity(asset) != self.identity:
                raise AssetWorkSuperseded(self)
            if asset.content_item_id is not None:
                content = session.scalar(select(ContentItem).where(
                    ContentItem.id == asset.content_item_id,
                    ContentItem.workspace_id == self.workspace_id,
                ).execution_options(populate_existing=True))
                if content is None or content.version != asset.content_version or content.status != "approved":
                    raise AssetWorkSuperseded(self)
            return asset


class AssetWorkSuperseded(RuntimeError):
    """A known result no longer belongs to the current asset/content state."""

    def __init__(self, work: AssetWork):
        self.work = work
        super().__init__("素材或内容已变化，旧结果未关联；已写对象保留待核对")
