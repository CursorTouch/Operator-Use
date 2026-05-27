"""CDP Storage Events"""
from __future__ import annotations
from typing import TypedDict, NotRequired, Required, Literal, Any, Dict, Union, Optional, List, Set, Tuple

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from operator_use.browser.cdp.protocol.network.types import RequestId
    from operator_use.browser.cdp.protocol.network.types import TimeSinceEpoch
    from operator_use.browser.cdp.protocol.page.types import FrameId
    from operator_use.browser.cdp.protocol.storage.types import InterestGroupAccessType
    from operator_use.browser.cdp.protocol.storage.types import InterestGroupAuctionEventType
    from operator_use.browser.cdp.protocol.storage.types import InterestGroupAuctionFetchType
    from operator_use.browser.cdp.protocol.storage.types import InterestGroupAuctionId
    from operator_use.browser.cdp.protocol.storage.types import SharedStorageAccessMethod
    from operator_use.browser.cdp.protocol.storage.types import SharedStorageAccessParams
    from operator_use.browser.cdp.protocol.storage.types import SharedStorageAccessScope
    from operator_use.browser.cdp.protocol.storage.types import StorageBucketInfo
    from operator_use.browser.cdp.protocol.target.types import TargetID

class cacheStorageContentUpdatedEvent(TypedDict, total=True):
    origin: str
    """Origin to update."""
    storageKey: str
    """Storage key to update."""
    bucketId: str
    """Storage bucket to update."""
    cacheName: str
    """Name of cache in origin."""
class cacheStorageListUpdatedEvent(TypedDict, total=True):
    origin: str
    """Origin to update."""
    storageKey: str
    """Storage key to update."""
    bucketId: str
    """Storage bucket to update."""
class indexedDBContentUpdatedEvent(TypedDict, total=True):
    origin: str
    """Origin to update."""
    storageKey: str
    """Storage key to update."""
    bucketId: str
    """Storage bucket to update."""
    databaseName: str
    """Database to update."""
    objectStoreName: str
    """ObjectStore to update."""
class indexedDBListUpdatedEvent(TypedDict, total=True):
    origin: str
    """Origin to update."""
    storageKey: str
    """Storage key to update."""
    bucketId: str
    """Storage bucket to update."""
class interestGroupAccessedEvent(TypedDict, total=True):
    accessTime: TimeSinceEpoch
    type: InterestGroupAccessType
    ownerOrigin: str
    name: str
    componentSellerOrigin: NotRequired[str]
    """For topLevelBid/topLevelAdditionalBid, and when appropriate, win and additionalBidWin"""
    bid: NotRequired[float]
    """For bid or somethingBid event, if done locally and not on a server."""
    bidCurrency: NotRequired[str]
    uniqueAuctionId: NotRequired[InterestGroupAuctionId]
    """For non-global events --- links to interestGroupAuctionEvent"""
class interestGroupAuctionEventOccurredEvent(TypedDict, total=True):
    eventTime: TimeSinceEpoch
    type: InterestGroupAuctionEventType
    uniqueAuctionId: InterestGroupAuctionId
    parentAuctionId: NotRequired[InterestGroupAuctionId]
    """Set for child auctions."""
    auctionConfig: NotRequired[Dict[str, Any]]
    """Set for started and configResolved"""
class interestGroupAuctionNetworkRequestCreatedEvent(TypedDict, total=True):
    type: InterestGroupAuctionFetchType
    requestId: RequestId
    auctions: List[InterestGroupAuctionId]
    """This is the set of the auctions using the worklet that issued this request.  In the case of trusted signals, it's possible that only some of them actually care about the keys being queried."""
class sharedStorageAccessedEvent(TypedDict, total=True):
    accessTime: TimeSinceEpoch
    """Time of the access."""
    scope: SharedStorageAccessScope
    """Enum value indicating the access scope."""
    method: SharedStorageAccessMethod
    """Enum value indicating the Shared Storage API method invoked."""
    mainFrameId: FrameId
    """DevTools Frame Token for the primary frame tree's root."""
    ownerOrigin: str
    """Serialization of the origin owning the Shared Storage data."""
    ownerSite: str
    """Serialization of the site owning the Shared Storage data."""
    params: SharedStorageAccessParams
    """The sub-parameters wrapped by params are all optional and their presence/absence depends on type."""
class sharedStorageWorkletOperationExecutionFinishedEvent(TypedDict, total=True):
    finishedTime: TimeSinceEpoch
    """Time that the operation finished."""
    executionTime: int
    """Time, in microseconds, from start of shared storage JS API call until end of operation execution in the worklet."""
    method: SharedStorageAccessMethod
    """Enum value indicating the Shared Storage API method invoked."""
    operationId: str
    """ID of the operation call."""
    workletTargetId: TargetID
    """Hex representation of the DevTools token used as the TargetID for the associated shared storage worklet."""
    mainFrameId: FrameId
    """DevTools Frame Token for the primary frame tree's root."""
    ownerOrigin: str
    """Serialization of the origin owning the Shared Storage data."""
class storageBucketCreatedOrUpdatedEvent(TypedDict, total=True):
    bucketInfo: StorageBucketInfo
class storageBucketDeletedEvent(TypedDict, total=True):
    bucketId: str