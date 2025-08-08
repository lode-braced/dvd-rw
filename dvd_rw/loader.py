import os.path
from os import PathLike
from typing import Callable

from .models import DVD, Matcher, Request
from .patcher import push_dvd, pop_dvd


class DVDLoader:
    def __init__(
        self,
        file_path: PathLike,
        match_on: list[Matcher],
        extra_matchers: list[Callable[[Request, Request], bool]],
    ):
        self.file_path = file_path
        self.dvd = None
        self.match_on = match_on
        self.extra_matchers = extra_matchers

    def load(self):
        """
        Load a DVD instance, creating one from file or a new empty instance if the file does not exist (yet).
        """
        if not os.path.isfile(self.file_path):
            dvd_instance = DVD(
                from_file=False,
                match_on=self.match_on,
                extra_matchers=self.extra_matchers,
                recorded_requests=[],
            )
        else:
            with open(self.file_path, "r") as f:
                dvd_instance = DVD.model_validate_json(f.read())
            # Override matchers from loader preferences
            dvd_instance.match_on = self.match_on
            dvd_instance.extra_matchers = self.extra_matchers
            # Mark as from_file to enforce replay-only semantics on loaded DVDs
            dvd_instance.from_file = True
            # Rebuild index to enable fast lookups on loaded data
            try:
                dvd_instance.rebuild_index()
            except AttributeError:
                # older models may not have this method; ignore
                pass
        self.dvd = dvd_instance

    def save(self):
        """
        Save the current DVD instance to file.
        """
        with open(self.file_path, "w") as f:
            f.write(self.dvd.model_dump_json())

    def __enter__(self):
        self.load()
        # Activate httpx patching for this DVD
        push_dvd(self.dvd)
        return self.dvd

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            if not exc_type and self.dvd and self.dvd.dirty:
                self.save()
                self.dvd.dirty = False
        finally:
            # Deactivate patching for this DVD
            if self.dvd is not None:
                pop_dvd(self.dvd)

    def _reusable_enter(self):
        if not self.dvd:
            self.load()
        # Activate httpx patching for this DVD (supports nested usage)
        push_dvd(self.dvd)

    def _reusable_exit(self):
        # save the dvd instance each time.
        if self.dvd.dirty:
            self.save()
            self.dvd.dirty = False
        # Deactivate patching for this DVD (supports nested usage)
        if self.dvd is not None:
            pop_dvd(self.dvd)
