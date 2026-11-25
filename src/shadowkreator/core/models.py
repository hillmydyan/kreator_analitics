"""Domain models and status enumerations matching PRD specifications."""

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class DeviceStatus(str, Enum):
    """Device connection status values (PRD Section 3.1)."""

    CONNECTED = "Connected"
    DISCONNECTED = "Disconnected"
    UNAUTHORIZED = "Unauthorized"
    OFFLINE = "Offline"
    PAIRING_REQUIRED = "Pairing Required"
    ERROR = "Error"


class ConnectionType(str, Enum):
    """Android connection methods (PRD Section 3.1)."""

    USB = "USB"
    WIRELESS = "Wireless"


class JobStatus(str, Enum):
    """Scraping job lifecycle states (PRD Section 4.4 & Section 7)."""

    PENDING = "Pending"
    INITIALIZING = "Initializing"
    SCANNING_VIDEOS = "Scanning Videos"
    EXTRACTING_VIDEO_DATA = "Extracting Video Data"
    EXTRACTING_PRODUCTS = "Extracting Products"
    PROCESSING_DATA = "Processing Data"
    COMPLETED = "Completed"
    COMPLETED_WITH_WARNINGS = "Completed With Warnings"
    FAILED = "Failed"
    CANCELLED = "Cancelled"
    INTERRUPTED = "Interrupted"


class ExtractionStatus(str, Enum):
    """Product/video extraction status values (PRD Section 3.4 & 7.3)."""

    SUCCESS = "Success"
    LINK_UNAVAILABLE = "Link Unavailable"
    NO_PRODUCT = "No Product"
    AMBIGUOUS = "Ambiguous"
    FAILED = "Failed"


class LogLevel(str, Enum):
    """Log levels for scraping_logs table (PRD Section 6.6)."""

    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


@dataclass
class DeviceInfo:
    """Represents a detected Android device (PRD Section 3.1 & 6.1)."""

    device_serial: str
    device_name: str
    model: str
    android_version: str
    connection_type: str
    connection_status: str
    sdk_int: Optional[int] = None
    last_connected_at: Optional[str] = None
    id: Optional[int] = None

    @property
    def major_android_version(self) -> Optional[int]:
        """Parse major Android version integer if available."""
        if not self.android_version:
            return None
        head = self.android_version.strip().split(".")[0]
        return int(head) if head.isdigit() else None

    @property
    def is_supported_android(self) -> bool:
        """Check whether device meets Android 12+ (API 31+) requirement (PRD Section 2.2)."""
        if self.sdk_int is not None:
            return self.sdk_int >= 31
        major = self.major_android_version
        if major is not None:
            return major >= 12
        return False

    @property
    def is_ready_for_scraping(self) -> bool:
        """PRD 3.1: Job may only start if connected, authorized, and Android 12+."""
        return (
            self.connection_status == DeviceStatus.CONNECTED.value
            and self.is_supported_android
        )


@dataclass
class ScrapingJobRecord:
    """Represents a row in scraping_jobs (PRD Section 6.2)."""

    creator_username: str
    period_days: int
    start_date: str
    end_date: str
    started_at: str
    completed_at: Optional[str] = None
    total_videos: int = 0
    processed_videos: int = 0
    failed_videos: int = 0
    total_products: int = 0
    job_status: str = JobStatus.PENDING.value
    error_message: Optional[str] = None
    id: Optional[int] = None


@dataclass
class VideoRecord:
    """Represents a row in videos (PRD Section 3.3 & 6.3)."""

    creator_username: str
    video_id: Optional[str]
    video_url: str
    upload_date: Optional[str]
    views: Optional[int]
    has_product: bool
    scraped_at: str
    job_id: Optional[int] = None
    product_count: int = 0
    id: Optional[int] = None


@dataclass
class ProductRecord:
    """Represents a row in products (PRD Section 3.4, 3.5 & 6.4)."""

    product_name: str
    normalized_name: str
    product_url: Optional[str]
    created_at: str
    updated_at: str
    is_ambiguous: bool = False
    id: Optional[int] = None


@dataclass
class VideoProductRecord:
    """Represents a row in video_products (PRD Section 6.5)."""

    video_id: int
    product_id: int
    views_snapshot: Optional[int]
    extraction_status: str
    scraped_at: str
    job_id: Optional[int] = None
    id: Optional[int] = None


@dataclass
class ScrapingLogRecord:
    """Represents a row in scraping_logs (PRD Section 6.6)."""

    job_id: int
    log_level: str
    action: str
    message: str
    created_at: str
    video_id: Optional[str] = None
    id: Optional[int] = None
