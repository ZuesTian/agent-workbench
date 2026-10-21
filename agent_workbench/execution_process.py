"""Own a CLI process and its descendants, including on manager termination."""

from __future__ import annotations

import os
import signal
import subprocess


class ProcessTree:
    def __init__(self, command, cwd, stdin, stdout, stderr, env=None):
        self.job = None
        self.process = None
        options = dict(
            cwd=cwd, stdin=stdin, stdout=stdout, stderr=stderr, shell=False, env=env,
        )
        try:
            if os.name == "nt":
                import psutil
                import pywintypes  # noqa: F401 - initialize the frozen pywin32 runtime.
                import win32job
                import win32process

                # Assign before the child runs, so even very early descendants
                # belong to this job. Closing the server kills this job only.
                self.job = win32job.CreateJobObject(None, "")
                limits = win32job.QueryInformationJobObject(
                    self.job, win32job.JobObjectExtendedLimitInformation
                )
                limits["BasicLimitInformation"]["LimitFlags"] |= (
                    win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
                )
                win32job.SetInformationJobObject(
                    self.job, win32job.JobObjectExtendedLimitInformation, limits
                )
                self.process = subprocess.Popen(
                    command,
                    creationflags=(
                        subprocess.CREATE_NO_WINDOW
                        | win32process.CREATE_SUSPENDED
                    ),
                    **options,
                )
                win32job.AssignProcessToJobObject(self.job, int(self.process._handle))
                psutil.Process(self.process.pid).resume()
            else:
                self.process = subprocess.Popen(command, start_new_session=True, **options)
        except Exception:
            if self.process is not None:
                self.process.kill()
                self.process.wait(timeout=5)
            self.close()
            raise

    def stop(self):
        if self.job is not None:
            import win32job

            win32job.TerminateJobObject(self.job, 1)
        elif self.process is not None:
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def close(self):
        if self.job is not None:
            self.job.Close()
            self.job = None
        elif self.process is not None and os.name != "nt":
            self.stop()
