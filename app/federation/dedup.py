"""
Content deduplication engine for federation
Detects and links duplicate entities by content hash and semantic matching
"""

import hashlib
from typing import Optional, Tuple, List, Dict, Any
from difflib import SequenceMatcher


class DeduplicationEngine:
    """Handles intelligent deduplication of federated entities"""
    
    @staticmethod
    def compute_content_hash(content: bytes) -> str:
        """Compute SHA-256 hash of content"""
        return f"sha256:{hashlib.sha256(content).hexdigest()}"
    
    @staticmethod
    def compute_text_hash(text: str) -> str:
        """Compute SHA-256 hash of text (normalized)"""
        normalized = text.strip().lower()
        return f"sha256:{hashlib.sha256(normalized.encode('utf-8')).hexdigest()}"
    
    @staticmethod
    def similarity_ratio(text1: str, text2: str) -> float:
        """Calculate similarity ratio between two strings (0.0 to 1.0)"""
        return SequenceMatcher(None, text1.lower(), text2.lower()).ratio()
    
    @classmethod
    def check_duplicate_document(cls, remote_doc: Dict[str, Any], 
                                 local_docs: List[Dict[str, Any]]) -> Tuple[Optional[str], Optional[str]]:
        """
        Check if remote document matches any local document
        Returns: (local_id, match_type) or (None, None)
        """
        remote_checksum = remote_doc.get('checksum')
        remote_size = remote_doc.get('size_bytes', 0)
        
        for local_doc in local_docs:
            # 1. Exact checksum match (highest confidence)
            if local_doc.get('checksum') == remote_checksum:
                return local_doc['local_id'], "exact_checksum_match"
            
            # 2. Same size + similar filename (medium confidence)
            if (local_doc.get('size_bytes') == remote_size and 
                cls.similarity_ratio(remote_doc.get('path', ''), local_doc.get('path', '')) > 0.8):
                return local_doc['local_id'], "size_filename_match"
        
        return None, None
    
    @classmethod
    def check_duplicate_contact(cls, remote_contact: Dict[str, Any],
                               local_contacts: List[Dict[str, Any]]) -> Tuple[Optional[str], Optional[str]]:
        """
        Check if remote contact matches any local contact
        Returns: (local_id, match_type) or (None, None)
        """
        remote_email = remote_contact.get('email', '').lower().strip()
        remote_phone = remote_contact.get('phone', '').strip()
        remote_name = remote_contact.get('display_name', '').lower().strip()
        remote_org = remote_contact.get('organization', '').lower().strip()
        
        for local_contact in local_contacts:
            local_email = local_contact.get('email', '').lower().strip()
            local_phone = local_contact.get('phone', '').strip()
            local_name = local_contact.get('display_name', '').lower().strip()
            local_org = local_contact.get('organization', '').lower().strip()
            
            # 1. Exact email match (highest confidence)
            if remote_email and remote_email == local_email:
                return local_contact['local_id'], "email_match"
            
            # 2. Exact phone match (high confidence)
            if remote_phone and remote_phone == local_phone:
                return local_contact['local_id'], "phone_match"
            
            # 3. Similar name + same organization (medium confidence)
            if (remote_org == local_org and remote_org and 
                cls.similarity_ratio(remote_name, local_name) > 0.85):
                return local_contact['local_id'], "name_org_match"
            
            # 4. Very similar name only (lower confidence)
            if cls.similarity_ratio(remote_name, local_name) > 0.95 and remote_name:
                return local_contact['local_id'], "name_similarity_match"
        
        return None, None
    
    @classmethod
    def check_duplicate_event(cls, remote_event: Dict[str, Any],
                             local_events: List[Dict[str, Any]]) -> Tuple[Optional[str], Optional[str]]:
        """
        Check if remote calendar event matches any local event
        Returns: (local_id, match_type) or (None, None)
        """
        remote_title = remote_event.get('title', '').lower().strip()
        remote_start = remote_event.get('start', '')
        remote_calendar = remote_event.get('calendar_id', '')
        
        for local_event in local_events:
            local_title = local_event.get('title', '').lower().strip()
            local_start = local_event.get('start', '')
            local_calendar = local_event.get('calendar_id', '')
            
            # 1. Exact match: same title, time, and calendar (highest confidence)
            if (remote_title == local_title and 
                remote_start == local_start and 
                remote_calendar == local_calendar):
                return local_event['local_id'], "exact_event_match"
            
            # 2. Same time and calendar (medium confidence)
            if (remote_start == local_start and 
                remote_calendar == local_calendar and 
                cls.similarity_ratio(remote_title, local_title) > 0.9):
                return local_event['local_id'], "time_calendar_match"
        
        return None, None
    
    @classmethod
    def check_duplicate_task(cls, remote_task: Dict[str, Any],
                            local_tasks: List[Dict[str, Any]]) -> Tuple[Optional[str], Optional[str]]:
        """
        Check if remote task matches any local task
        Returns: (local_id, match_type) or (None, None)
        """
        remote_title = remote_task.get('title', '').lower().strip()
        remote_due = remote_task.get('due_date', '')
        remote_status = remote_task.get('status', '')
        
        for local_task in local_tasks:
            local_title = local_task.get('title', '').lower().strip()
            local_due = local_task.get('due_date', '')
            local_status = local_task.get('status', '')
            
            # 1. Exact match: same title and due date (high confidence)
            if (remote_title == local_title and remote_due == local_due):
                return local_task['local_id'], "exact_task_match"
            
            # 2. Very similar title, same status (medium confidence)
            if (cls.similarity_ratio(remote_title, local_title) > 0.95 and 
                remote_status == local_status):
                return local_task['local_id'], "title_status_match"
        
        return None, None


class VersionConflictDetector:
    """Detects version conflicts between remote and local entities"""
    
    @staticmethod
    def has_conflict(remote_version: int, local_version: int,
                     remote_modified: str, local_modified: str) -> bool:
        """
        Detect if versions are in conflict
        Returns True if both sides have independent changes
        """
        # Different versions (both have been modified independently)
        return (remote_version != local_version or 
                remote_modified != local_modified)
    
    @staticmethod
    def resolve_by_timestamp(remote_modified: str, local_modified: str,
                            tiebreaker_peer_id: Optional[str] = None) -> str:
        """
        Resolve conflict by newer timestamp
        Returns: 'remote', 'local', or 'equal'
        """
        if remote_modified > local_modified:
            return 'remote'
        elif remote_modified < local_modified:
            return 'local'
        else:
            # Same timestamp - use peer_id as tiebreaker
            return 'equal'
    
    @staticmethod
    def resolve_by_version(remote_version: int, local_version: int) -> str:
        """
        Resolve conflict by version number (higher wins)
        Returns: 'remote', 'local', or 'equal'
        """
        if remote_version > local_version:
            return 'remote'
        elif remote_version < local_version:
            return 'local'
        else:
            return 'equal'


class MergeEngine:
    """Handles merging of conflicting entities (especially contacts and events)"""
    
    @staticmethod
    def merge_contact_fields(remote_contact: Dict[str, Any],
                            local_contact: Dict[str, Any]) -> Dict[str, Any]:
        """
        Intelligently merge contact fields from both versions
        Remote fields override empty local fields; non-empty local fields preserved
        """
        merged = local_contact.copy()
        
        # Fields to merge
        mergeable_fields = ['email', 'phone', 'organization', 'address', 
                           'city', 'postal_code', 'country', 'notes']
        
        for field in mergeable_fields:
            remote_val = remote_contact.get(field, '')
            local_val = merged.get(field, '')
            
            # If local is empty, take remote
            if not local_val and remote_val:
                merged[field] = remote_val
            # If both exist and different, keep local (avoid overwrite)
            elif local_val and remote_val and local_val != remote_val:
                # Could log this as a merge note for manual review
                pass
        
        # Update metadata
        merged['modified_at'] = max(
            remote_contact.get('modified_at', ''),
            local_contact.get('modified_at', '')
        )
        merged['version'] = max(
            remote_contact.get('version', 0),
            local_contact.get('version', 0)
        ) + 1
        
        return merged
    
    @staticmethod
    def merge_events(remote_event: Dict[str, Any],
                    local_event: Dict[str, Any]) -> Dict[str, Any]:
        """
        Merge calendar events - keep both if different, merge details if same base
        """
        # If exact same event, return local (already synced)
        if remote_event.get('source_id') == local_event.get('source_id'):
            return local_event
        
        # If coming from different sources, this is a merge scenario
        # Usually handled by caller deciding to keep both or use conflict strategy
        merged = local_event.copy()
        
        # Update attendees if remote has more
        if 'attendees' in remote_event:
            local_attendees = set(merged.get('attendees', []))
            remote_attendees = set(remote_event.get('attendees', []))
            merged['attendees'] = list(local_attendees | remote_attendees)
        
        return merged
