import os.path
from os import PathLike
from typing import Unpack

from pydantic import TypeAdapter

from .models import DVD, Request, Response, RequestExceptionInfo, DVDKwargs
from .patcher import push_dvd, pop_dvd


_RECORDING_LOADER = TypeAdapter(list[tuple[Request, Response | RequestExceptionInfo]])


class DVDLoader:
    def __init__(
        self,
        file_path: PathLike,
        **dvd_kwargs: Unpack[DVDKwargs],
    ):
        self.file_path = file_path
        self.dvd = None
        # Build and store DVD kwargs in a single place for unified typing/usage
        self._dvd_kwargs: DVDKwargs = dvd_kwargs

    def load(self):
        """
        Load a DVD instance, creating one from file or a new empty instance if the file does not exist (yet).
        Uses Pydantic TypeAdapters to deserialize recorded request/value pairs.
        """
        if not os.path.isfile(self.file_path):
            # New/empty DVD
            recorded = []
            from_file = False
        else:
            # Read bytes to support TypeAdapter.validate_json
            with open(self.file_path, "rb") as f:
                raw = f.read()
            recorded = _RECORDING_LOADER.validate_json(raw)
            from_file = True
        dvd_instance = DVD(
            recorded_requests=recorded, from_file=from_file, **self._dvd_kwargs
        )  # type: ignore[arg-type]
        self.dvd = dvd_instance

    def save(self):
        """
        Save the current DVD instance to file using a TypeAdapter for the recorded pairs.
        """
        ta = TypeAdapter(list[tuple[Request, Response | RequestExceptionInfo]])
        json_bytes = ta.dump_json(self.dvd.recorded_requests)
        with open(self.file_path, "wb") as f:
            f.write(json_bytes)

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
