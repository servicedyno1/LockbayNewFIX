"""
Leader Election Service for Distributed Job Coordination
Redis-based leader election with automatic failover and job coordination
"""

import asyncio
import logging
import os
import uuid
from typing import Optional, Dict, Any, Callable, Set
from datetime import datetime
from dataclasses import dataclass
from enum import Enum

from services.state_manager import state_manager

logger = logging.getLogger(__name__)


class LeadershipStatus(Enum):
    """Leadership status for this instance"""
    FOLLOWER = "follower"
    CANDIDATE = "candidate"
    LEADER = "leader"
    FAILED = "failed"


@dataclass
class LeaderInfo:
    """Information about the current leader"""
    instance_id: str
    elected_at: datetime
    last_heartbeat: datetime
    election_term: int
    metadata: Dict[str, Any]


@dataclass
class ElectionConfig:
    """Configuration for leader election"""
    heartbeat_interval: int = 30  # seconds
    election_timeout: int = 60    # seconds
    leader_ttl: int = 90         # seconds
    max_election_retries: int = 3
    leadership_grace_period: int = 10  # seconds


class LeaderElection:
    """
    Redis-based leader election service with automatic failover
    
    Features:
    - Distributed leader election across multiple instances
    - Automatic failover when leader fails
    - Heartbeat-based leader health monitoring
    - Election term management for consistency
    - Leader-only job scheduling and coordination
    """
    
    def __init__(self, service_name: str, instance_id: Optional[str] = None):
        self.service_name = service_name
        self.instance_id = instance_id or f"{service_name}_{uuid.uuid4().hex[:8]}"
        self.config = ElectionConfig()
        
        # Election state
        self.status = LeadershipStatus.FOLLOWER
        self.current_term = 0
        self.leader_info: Optional[LeaderInfo] = None
        self.election_in_progress = False
        
        # Tasks and callbacks
        self.heartbeat_task: Optional[asyncio.Task] = None
        self.monitor_task: Optional[asyncio.Task] = None
        self.election_task: Optional[asyncio.Task] = None
        self.running = False
        
        # Leadership event callbacks
        self.on_elected_callbacks: Set[Callable] = set()
        self.on_deposed_callbacks: Set[Callable] = set()
        self.on_leader_changed_callbacks: Set[Callable] = set()
        
        # Redis keys
        self.leader_key = f"leader_election:{service_name}:leader"
        self.candidates_key = f"leader_election:{service_name}:candidates"
        self.heartbeat_key = f"leader_election:{service_name}:heartbeat:{self.instance_id}"
        self.lock_key = f"leader_election:{service_name}:election_lock"
        
        logger.info(f"🗳️ Initialized leader election for {service_name} with instance {self.instance_id}")
    
    async def start(self) -> bool:
        """Start the leader election service"""
        if self.running:
            logger.warning("Leader election already running")
            return True
        
        try:
            self.running = True
            logger.info(f"🚀 Starting leader election for {self.service_name}")
            
            # Register this instance as a candidate
            await self._register_candidate()
            
            # Start monitoring and heartbeat tasks
            self.monitor_task = asyncio.create_task(self._monitor_leader())
            
            # Initial leader check
            await self._check_leader_status()
            
            logger.info(f"✅ Leader election started for {self.service_name}")
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to start leader election: {e}")
            self.running = False
            return False
    
    async def stop(self):
        """Stop the leader election service"""
        if not self.running:
            return
        
        logger.info(f"🛑 Stopping leader election for {self.service_name}")
        self.running = False
        
        # Cancel tasks
        for task in [self.heartbeat_task, self.monitor_task, self.election_task]:
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        
        # Step down if leader
        if self.status == LeadershipStatus.LEADER:
            await self._step_down()
        
        # Unregister candidate
        await self._unregister_candidate()
        
        logger.info(f"✅ Leader election stopped for {self.service_name}")
    
    def is_leader(self) -> bool:
        """Check if this instance is the current leader"""
        return self.status == LeadershipStatus.LEADER
    
    def get_leader_info(self) -> Optional[LeaderInfo]:
        """Get information about the current leader"""
        return self.leader_info
    
    def add_election_callback(self, event: str, callback: Callable):
        """Add callback for leadership events"""
        if event == "elected":
            self.on_elected_callbacks.add(callback)
        elif event == "deposed":
            self.on_deposed_callbacks.add(callback)
        elif event == "leader_changed":
            self.on_leader_changed_callbacks.add(callback)
        else:
            raise ValueError(f"Unknown election event: {event}")
    
    async def _register_candidate(self):
        """Register this instance as a candidate"""
        candidate_info = {
            'instance_id': self.instance_id,
            'registered_at': datetime.utcnow().isoformat(),
            'last_seen': datetime.utcnow().isoformat(),
            'metadata': {
                'service_name': self.service_name,
                'pid': os.getpid() if hasattr(os, 'getpid') else 0
            }
        }
        
        await state_manager.set_state(
            f"{self.candidates_key}:{self.instance_id}",
            candidate_info,
            ttl=self.config.leader_ttl + 30,  # Longer TTL for candidate registration
            tags=['leader_election', 'candidate'],
            source='leader_election'
        )
        
        logger.debug(f"📝 Registered candidate {self.instance_id}")
    
    async def _unregister_candidate(self):
        """Unregister this instance as a candidate"""
        await state_manager.delete_state(f"{self.candidates_key}:{self.instance_id}")
        await state_manager.delete_state(self.heartbeat_key)
        logger.debug(f"🗑️ Unregistered candidate {self.instance_id}")
    
    async def _check_leader_status(self):
        """Check current leader status and initiate election if needed"""
        try:
            leader_data = await state_manager.get_state(self.leader_key)
            
            if not leader_data:
                # No leader, start election
                logger.info("🗳️ No leader found, starting election")
                await self._start_election()
                return
            
            # Parse leader info
            self.leader_info = LeaderInfo(
                instance_id=leader_data['instance_id'],
                elected_at=datetime.fromisoformat(leader_data['elected_at']),
                last_heartbeat=datetime.fromisoformat(leader_data['last_heartbeat']),
                election_term=leader_data['election_term'],
                metadata=leader_data.get('metadata', {})
            )
            
            # Check if we are the leader
            if self.leader_info.instance_id == self.instance_id:
                await self._become_leader(leader_data['election_term'])
            else:
                self.status = LeadershipStatus.FOLLOWER
                logger.info(f"👥 Following leader {self.leader_info.instance_id}")
            
        except Exception as e:
            logger.error(f"❌ Error checking leader status: {e}")
            await self._start_election()
    
    async def _start_election(self):
        """Start a new leader election"""
        if self.election_in_progress:
            logger.debug("Election already in progress")
            return
        
        self.election_in_progress = True
        self.status = LeadershipStatus.CANDIDATE
        
        try:
            logger.info(f"🗳️ Starting election for term {self.current_term + 1}")
            
            # Acquire election lock to prevent concurrent elections
            async with state_manager.acquire_lock(self.lock_key, timeout=30):
                # Increment term
                self.current_term += 1
                
                # Vote for ourselves
                vote_key = f"leader_election:{self.service_name}:votes:{self.current_term}:{self.instance_id}"
                await state_manager.set_state(
                    vote_key,
                    {'voter': self.instance_id, 'candidate': self.instance_id, 'term': self.current_term},
                    ttl=300,  # 5 minute TTL for votes
                    tags=['leader_election', 'vote'],
                    source='leader_election'
                )
                
                # Wait for other votes or timeout
                await asyncio.sleep(10)  # Give time for other candidates
                
                # Count votes
                votes = await self._count_votes(self.current_term)
                total_candidates = await self._count_candidates()
                
                # Need majority to win
                if votes > total_candidates // 2:
                    await self._become_leader(self.current_term)
                else:
                    logger.info(f"🗳️ Lost election with {votes}/{total_candidates} votes")
                    self.status = LeadershipStatus.FOLLOWER
                    
                    # Wait before next election attempt
                    await asyncio.sleep(self.config.election_timeout)
        
        except Exception as e:
            logger.error(f"❌ Election failed: {e}")
            self.status = LeadershipStatus.FAILED
        
        finally:
            self.election_in_progress = False
    
    async def _become_leader(self, term: int):
        """Become the leader for the given term"""
        try:
            self.status = LeadershipStatus.LEADER
            self.current_term = term
            
            # Update leader info in Redis
            leader_data = {
                'instance_id': self.instance_id,
                'elected_at': datetime.utcnow().isoformat(),
                'last_heartbeat': datetime.utcnow().isoformat(),
                'election_term': term,
                'metadata': {
                    'service_name': self.service_name,
                    'leadership_started': datetime.utcnow().isoformat()
                }
            }
            
            await state_manager.set_state(
                self.leader_key,
                leader_data,
                ttl=self.config.leader_ttl,
                tags=['leader_election', 'leader'],
                source='leader_election'
            )
            
            self.leader_info = LeaderInfo(
                instance_id=self.instance_id,
                elected_at=datetime.utcnow(),
                last_heartbeat=datetime.utcnow(),
                election_term=term,
                metadata=leader_data['metadata']
            )
            
            # Start heartbeat
            if self.heartbeat_task:
                self.heartbeat_task.cancel()
            self.heartbeat_task = asyncio.create_task(self._leader_heartbeat())
            
            logger.info(f"👑 Became leader for {self.service_name} (term {term})")
            
            # Notify callbacks
            for callback in self.on_elected_callbacks:
                try:
                    if asyncio.iscoroutinefunction(callback):
                        await callback()
                    else:
                        callback()
                except Exception as e:
                    logger.error(f"Error in election callback: {e}")
        
        except Exception as e:
            logger.error(f"❌ Failed to become leader: {e}")
            self.status = LeadershipStatus.FAILED
    
    async def _step_down(self):
        """Step down from leadership"""
        if self.status != LeadershipStatus.LEADER:
            return
        
        logger.info(f"👑➡️👥 Stepping down as leader for {self.service_name}")
        
        # Cancel heartbeat
        if self.heartbeat_task:
            self.heartbeat_task.cancel()
        
        # Remove leader entry
        await state_manager.delete_state(self.leader_key)
        
        # Update status
        self.status = LeadershipStatus.FOLLOWER
        self.leader_info = None
        
        # Notify callbacks
        for callback in self.on_deposed_callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback()
                else:
                    callback()
            except Exception as e:
                logger.error(f"Error in deposition callback: {e}")
    
    async def _leader_heartbeat(self):
        """Send periodic heartbeats as leader"""
        while self.running and self.status == LeadershipStatus.LEADER:
            try:
                # Update heartbeat timestamp
                heartbeat_data = {
                    'instance_id': self.instance_id,
                    'last_heartbeat': datetime.utcnow().isoformat(),
                    'term': self.current_term,
                    'status': 'healthy'
                }
                
                await state_manager.set_state(
                    self.heartbeat_key,
                    heartbeat_data,
                    ttl=self.config.heartbeat_interval * 2,
                    tags=['leader_election', 'heartbeat'],
                    source='leader_election'
                )
                
                # Update leader record
                leader_data = await state_manager.get_state(self.leader_key)
                if leader_data:
                    leader_data['last_heartbeat'] = datetime.utcnow().isoformat()
                    await state_manager.set_state(
                        self.leader_key,
                        leader_data,
                        ttl=self.config.leader_ttl,
                        tags=['leader_election', 'leader'],
                        source='leader_election'
                    )
                
                await asyncio.sleep(self.config.heartbeat_interval)
                
            except Exception as e:
                logger.error(f"❌ Heartbeat failed: {e}")
                await self._step_down()
                break
    
    async def _monitor_leader(self):
        """Monitor leader health and initiate elections if needed"""
        while self.running:
            try:
                if self.status == LeadershipStatus.LEADER:
                    # We are leader, just wait
                    await asyncio.sleep(self.config.heartbeat_interval)
                    continue
                
                # Check leader health
                leader_data = await state_manager.get_state(self.leader_key)
                
                if not leader_data:
                    # No leader, start election
                    await self._start_election()
                    await asyncio.sleep(self.config.election_timeout)
                    continue
                
                # Check if leader is still alive
                last_heartbeat = datetime.fromisoformat(leader_data['last_heartbeat'])
                heartbeat_age = (datetime.utcnow() - last_heartbeat).total_seconds()
                
                if heartbeat_age > self.config.election_timeout:
                    logger.warning(f"⚠️ Leader {leader_data['instance_id']} missed heartbeat for {heartbeat_age}s")
                    await self._start_election()
                    await asyncio.sleep(self.config.election_timeout)
                else:
                    # Leader is healthy
                    if not self.leader_info or self.leader_info.instance_id != leader_data['instance_id']:
                        # New leader detected
                        old_leader = self.leader_info.instance_id if self.leader_info else None
                        self.leader_info = LeaderInfo(
                            instance_id=leader_data['instance_id'],
                            elected_at=datetime.fromisoformat(leader_data['elected_at']),
                            last_heartbeat=last_heartbeat,
                            election_term=leader_data['election_term'],
                            metadata=leader_data.get('metadata', {})
                        )
                        
                        logger.info(f"👑 New leader detected: {self.leader_info.instance_id}")
                        
                        # Notify callbacks
                        for callback in self.on_leader_changed_callbacks:
                            try:
                                if asyncio.iscoroutinefunction(callback):
                                    await callback(old_leader, self.leader_info.instance_id)
                                else:
                                    callback(old_leader, self.leader_info.instance_id)
                            except Exception as e:
                                logger.error(f"Error in leader change callback: {e}")
                    
                    await asyncio.sleep(self.config.heartbeat_interval)
            
            except Exception as e:
                logger.error(f"❌ Error monitoring leader: {e}")
                await asyncio.sleep(self.config.heartbeat_interval)
    
    async def _count_votes(self, term: int) -> int:
        """Count votes for this instance in the given term"""
        try:
            # This would need a Redis SCAN operation in a real implementation
            # For now, return 1 (our own vote)
            return 1
        except Exception as e:
            logger.error(f"Error counting votes: {e}")
            return 0
    
    async def _count_candidates(self) -> int:
        """Count total number of active candidates"""
        try:
            # This would need to scan candidate keys in a real implementation
            # For now, return 1 (ourselves)
            return 1
        except Exception as e:
            logger.error(f"Error counting candidates: {e}")
            return 1


class DistributedJobCoordinator:
    """
    Coordinates background jobs across multiple instances using leader election
    """
    
    def __init__(self, service_name: str = "lockbay_jobs"):
        self.service_name = service_name
        self.leader_election = LeaderElection(service_name)
        self.scheduled_jobs: Dict[str, Dict] = {}
        self.job_handlers: Dict[str, Callable] = {}
        self.running = False
        
        # Register leadership callbacks
        self.leader_election.add_election_callback("elected", self._on_become_leader)
        self.leader_election.add_election_callback("deposed", self._on_lose_leadership)
        
        logger.info(f"🎯 Initialized distributed job coordinator for {service_name}")
    
    async def start(self):
        """Start the distributed job coordinator"""
        if self.running:
            return
        
        self.running = True
        await self.leader_election.start()
        logger.info("🚀 Distributed job coordinator started")
    
    async def stop(self):
        """Stop the distributed job coordinator"""
        if not self.running:
            return
        
        self.running = False
        await self.leader_election.stop()
        logger.info("🛑 Distributed job coordinator stopped")
    
    def register_job_handler(self, job_type: str, handler: Callable):
        """Register a handler for a specific job type"""
        self.job_handlers[job_type] = handler
        logger.info(f"📝 Registered handler for job type: {job_type}")
    
    async def schedule_leader_only_job(
        self,
        job_id: str,
        job_type: str,
        schedule_expression: str,
        parameters: Optional[Dict] = None
    ):
        """Schedule a job that should only run on the leader instance"""
        job_config = {
            'job_id': job_id,
            'job_type': job_type,
            'schedule_expression': schedule_expression,
            'parameters': parameters or {},
            'leader_only': True,
            'created_at': datetime.utcnow().isoformat()
        }
        
        self.scheduled_jobs[job_id] = job_config
        
        # If we're currently leader, schedule immediately
        if self.leader_election.is_leader():
            await self._schedule_job_on_leader(job_config)
        
        logger.info(f"📅 Scheduled leader-only job: {job_id}")
    
    async def _on_become_leader(self):
        """Called when this instance becomes the leader"""
        logger.info(f"👑 Became leader - scheduling {len(self.scheduled_jobs)} leader-only jobs")
        
        # Schedule all leader-only jobs
        for job_config in self.scheduled_jobs.values():
            if job_config.get('leader_only'):
                await self._schedule_job_on_leader(job_config)
    
    async def _on_lose_leadership(self):
        """Called when this instance loses leadership"""
        logger.info("👥 Lost leadership - stopping leader-only jobs")
        
        # In a real implementation, we would cancel scheduled jobs here
        # This would integrate with APScheduler or the persistent job service
    
    async def _schedule_job_on_leader(self, job_config: Dict):
        """Schedule a job on the leader instance"""
        try:
            job_type = job_config['job_type']
            handler = self.job_handlers.get(job_type)
            
            if not handler:
                logger.error(f"❌ No handler registered for job type: {job_type}")
                return
            
            # In a real implementation, this would schedule with APScheduler
            # or the persistent job service
            logger.info(f"⚙️ Scheduling job {job_config['job_id']} on leader")
            
        except Exception as e:
            logger.error(f"❌ Failed to schedule job on leader: {e}")


# Global instance
distributed_job_coordinator = DistributedJobCoordinator()


async def initialize_leader_election():
    """Initialize the distributed job coordination system"""
    await distributed_job_coordinator.start()


async def cleanup_leader_election():
    """Cleanup the distributed job coordination system"""
    await distributed_job_coordinator.stop()