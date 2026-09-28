"""Fall back to the safe page cursor if Scrapy's disk queue is unreadable."""
import logging
import pickle
from scrapy.core.scheduler import Scheduler

logger = logging.getLogger(__name__)
_CORRUPTION = (FileNotFoundError, ValueError, EOFError, pickle.UnpicklingError, KeyError, TypeError)


class RecoveringScheduler(Scheduler):
    def _dq(self):
        try:
            return super()._dq()
        except _CORRUPTION:
            logger.warning('Corrupt JOBDIR request queue; preserving files and using safe page replay')
            return None

    def _dqpop(self):
        try:
            return super()._dqpop()
        except _CORRUPTION:
            logger.warning('Corrupt queued request; preserving files and using safe page replay')
            self.dqs = None
            return None
