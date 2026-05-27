"""CDP Storage Types"""
from __future__ import annotations
from typing import TypedDict, NotRequired, Required, Literal, Any, Dict, Union, Optional, List, Set, Tuple

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from operator_use.browser.cdp.protocol.network.types import TimeSinceEpoch
    from operator_use.browser.cdp.protocol.target.types import TargetID

SerializedStorageKey = str
StorageType = Literal['cookies','file_systems','indexeddb','local_storage','shader_cache','websql','service_workers','cache_storage','interest_groups','shared_storage','storage_buckets','all','other']
"""Enum of possible storage types."""
class UsageForType(TypedDict, total=True):
    """Usage for a storage type."""
    storageType: StorageType
    """Name of storage type."""
    usage: float
    """Storage usage (bytes)."""
class TrustTokens(TypedDict, total=True):
    """Pair of issuer origin and number of available (signed, but not used) Trust Tokens from that issuer."""
    issuerOrigin: str
    count: float
InterestGroupAuctionId = str
"""Protected audience interest group auction identifier."""
InterestGroupAccessType = Literal['join','leave','update','loaded','bid','win','additionalBid','additionalBidWin','topLevelBid','topLevelAdditionalBid','clear']
"""Enum of interest group access types."""
InterestGroupAuctionEventType = Literal['started','configResolved']
"""Enum of auction events."""
InterestGroupAuctionFetchType = Literal['bidderJs','bidderWasm','sellerJs','bidderTrustedSignals','sellerTrustedSignals']
"""Enum of network fetches auctions can do."""
SharedStorageAccessScope = Literal['window','sharedStorageWorklet','protectedAudienceWorklet','header']
"""Enum of shared storage access scopes."""
SharedStorageAccessMethod = Literal['addModule','createWorklet','selectURL','run','batchUpdate','set','append','delete','clear','get','keys','values','entries','length','remainingBudget']
"""Enum of shared storage access methods."""
class SharedStorageEntry(TypedDict, total=True):
    """Struct for a single key-value pair in an origin's shared storage."""
    key: str
    value: str
class SharedStorageMetadata(TypedDict, total=True):
    """Details for an origin's shared storage."""
    creationTime: TimeSinceEpoch
    """Time when the origin's shared storage was last created."""
    length: int
    """Number of key-value pairs stored in origin's shared storage."""
    remainingBudget: float
    """Current amount of bits of entropy remaining in the navigation budget."""
    bytesUsed: int
    """Total number of bytes stored as key-value pairs in origin's shared storage."""
class SharedStoragePrivateAggregationConfig(TypedDict, total=True):
    """Represents a dictionary object passed in as privateAggregationConfig to run or selectURL."""
    filteringIdMaxBytes: int
    """Configures the maximum size allowed for filtering IDs."""
    aggregationCoordinatorOrigin: NotRequired[str]
    """The chosen aggregation service deployment."""
    contextId: NotRequired[str]
    """The context ID provided."""
    maxContributions: NotRequired[int]
    """The limit on the number of contributions in the final report."""
class SharedStorageReportingMetadata(TypedDict, total=True):
    """Pair of reporting metadata details for a candidate URL for selectURL()."""
    eventType: str
    reportingUrl: str
class SharedStorageUrlWithMetadata(TypedDict, total=True):
    """Bundles a candidate URL with its reporting metadata."""
    url: str
    """Spec of candidate URL."""
    reportingMetadata: List[SharedStorageReportingMetadata]
    """Any associated reporting metadata."""
class SharedStorageAccessParams(TypedDict, total=False):
    """Bundles the parameters for shared storage access events whose presence/absence can vary according to SharedStorageAccessType."""
    scriptSourceUrl: NotRequired[str]
    """Spec of the module script URL. Present only for SharedStorageAccessMethods: addModule and createWorklet."""
    dataOrigin: NotRequired[str]
    """String denoting "context-origin", "script-origin", or a custom origin to be used as the worklet's data origin. Present only for SharedStorageAccessMethod: createWorklet."""
    operationName: NotRequired[str]
    """Name of the registered operation to be run. Present only for SharedStorageAccessMethods: run and selectURL."""
    operationId: NotRequired[str]
    """ID of the operation call. Present only for SharedStorageAccessMethods: run and selectURL."""
    keepAlive: NotRequired[bool]
    """Whether or not to keep the worket alive for future run or selectURL calls. Present only for SharedStorageAccessMethods: run and selectURL."""
    privateAggregationConfig: NotRequired[SharedStoragePrivateAggregationConfig]
    """Configures the private aggregation options. Present only for SharedStorageAccessMethods: run and selectURL."""
    serializedData: NotRequired[str]
    """The operation's serialized data in bytes (converted to a string). Present only for SharedStorageAccessMethods: run and selectURL. TODO(crbug.com/401011862): Consider updating this parameter to binary."""
    urlsWithMetadata: NotRequired[List[SharedStorageUrlWithMetadata]]
    """Array of candidate URLs' specs, along with any associated metadata. Present only for SharedStorageAccessMethod: selectURL."""
    urnUuid: NotRequired[str]
    """Spec of the URN:UUID generated for a selectURL call. Present only for SharedStorageAccessMethod: selectURL."""
    key: NotRequired[str]
    """Key for a specific entry in an origin's shared storage. Present only for SharedStorageAccessMethods: set, append, delete, and get."""
    value: NotRequired[str]
    """Value for a specific entry in an origin's shared storage. Present only for SharedStorageAccessMethods: set and append."""
    ignoreIfPresent: NotRequired[bool]
    """Whether or not to set an entry for a key if that key is already present. Present only for SharedStorageAccessMethod: set."""
    workletOrdinal: NotRequired[int]
    """A number denoting the (0-based) order of the worklet's creation relative to all other shared storage worklets created by documents using the current storage partition. Present only for SharedStorageAccessMethods: addModule, createWorklet."""
    workletTargetId: NotRequired[TargetID]
    """Hex representation of the DevTools token used as the TargetID for the associated shared storage worklet. Present only for SharedStorageAccessMethods: addModule, createWorklet, run, selectURL, and any other SharedStorageAccessMethod when the SharedStorageAccessScope is sharedStorageWorklet."""
    withLock: NotRequired[str]
    """Name of the lock to be acquired, if present. Optionally present only for SharedStorageAccessMethods: batchUpdate, set, append, delete, and clear."""
    batchUpdateId: NotRequired[str]
    """If the method has been called as part of a batchUpdate, then this number identifies the batch to which it belongs. Optionally present only for SharedStorageAccessMethods: batchUpdate (required), set, append, delete, and clear."""
    batchSize: NotRequired[int]
    """Number of modifier methods sent in batch. Present only for SharedStorageAccessMethod: batchUpdate."""
StorageBucketsDurability = Literal['relaxed','strict']
class StorageBucket(TypedDict, total=True):
    storageKey: SerializedStorageKey
    name: NotRequired[str]
    """If not specified, it is the default bucket of the storageKey."""
class StorageBucketInfo(TypedDict, total=True):
    bucket: StorageBucket
    id: str
    expiration: TimeSinceEpoch
    quota: float
    """Storage quota (bytes)."""
    persistent: bool
    durability: StorageBucketsDurability
class RelatedWebsiteSet(TypedDict, total=True):
    """A single Related Website Set object."""
    primarySites: List[str]
    """The primary site of this set, along with the ccTLDs if there is any."""
    associatedSites: List[str]
    """The associated sites of this set, along with the ccTLDs if there is any."""
    serviceSites: List[str]
    """The service sites of this set, along with the ccTLDs if there is any."""