#    Copyright 2016 Mirantis, Inc.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

from __future__ import print_function
from __future__ import unicode_literals

import os
import fcntl
from subprocess import PIPE
from subprocess import Popen
from threading import Event
from time import sleep

from devops.error import TimeoutError
from devops.helpers.decorators import threaded
from devops.helpers.exec_result import ExecResult
from devops.helpers import subprocess_runner
from devops import logger


class Subprocess(subprocess_runner.Subprocess):
    """Extension of fuel-devops Subprocess class

    This is temporary solution that provides non-blocking read from
    stdout and stderr and log the data in real time.
    Should be removed when this code will be merged to fuel-devops.
    """

    @classmethod
    def __exec_command(cls, command, cwd=None, env=None, timeout=None,
                       verbose=True):
        """Command executor helper

        :type command: str
        :type cwd: str
        :type env: dict
        :type timeout: int
        :rtype: ExecResult
        """

        def readlines(stream, verbose, lines_count=100):
            """Nonblocking read and log lines from stream"""
            if lines_count < 1:
                lines_count = 1
            result = []
            try:
                for _ in range(1, lines_count):
                    line = stream.readline()
                    if line:
                        result.append(line)
                        if verbose:
                            print(line.rstrip())
            except IOError:
                pass
            return result

        @threaded(started=True)
        def poll_pipes(proc, result, stop):
            """Polling task for FIFO buffers

            :type proc: Popen
            :type result: ExecResult
            :type stop: Event
            """
            # Get file descriptors for stdout and stderr streams
            fd_stdout = proc.stdout.fileno()
            fd_stderr = proc.stderr.fileno()
            # Get flags of stdout and stderr streams
            fl_stdout = fcntl.fcntl(fd_stdout, fcntl.F_GETFL)
            fl_stderr = fcntl.fcntl(fd_stderr, fcntl.F_GETFL)
            # Set nonblock mode for stdout and stderr streams
            fcntl.fcntl(fd_stdout, fcntl.F_SETFL, fl_stdout | os.O_NONBLOCK)
            fcntl.fcntl(fd_stderr, fcntl.F_SETFL, fl_stderr | os.O_NONBLOCK)

            while not stop.isSet():
                sleep(0.1)

                stdout_diff = readlines(proc.stdout, verbose)
                stderr_diff = readlines(proc.stderr, verbose)
                result.stdout += stdout_diff
                result.stderr += stderr_diff

                proc.poll()

                if proc.returncode is not None:
                    result.exit_code = proc.returncode
                    stdout_diff = readlines(proc.stdout, verbose)
                    stderr_diff = readlines(proc.stderr, verbose)
                    result.stdout += stdout_diff
                    result.stderr += stderr_diff
                    stop.set()

        # 1 Command per run
        with cls.__lock:
            result = ExecResult(cmd=command)
            stop_event = Event()

            logger.debug("Run command on the host: '{0}'".format(command))

            # Run
            process = Popen(
                args=[command],
                stdin=PIPE, stdout=PIPE, stderr=PIPE,
                shell=True, cwd=cwd, env=env,
                universal_newlines=False)
            # Poll output
            poll_pipes(process, result, stop_event)
            # wait for process close
            stop_event.wait(timeout)

            output_tmpl = (
                '\tSTDOUT:\n'
                '{0}\n'
                '\tSTDERR"\n'
                '{1}\n')
            logger.debug(output_tmpl.format(result.stdout, result.stderr))

            # Process closed?
            if stop_event.isSet():
                stop_event.clear()
                return result

            # Kill not ended process and wait for close
            try:
                process.kill()  # kill -9
                stop_event.wait(5)

            except OSError:
                # Nothing to kill
                logger.warning(
                    "{} has been completed just after timeout: "
                    "please validate timeout.".format(command))

            no_ec_msg = (
                "No return code received while waiting for the command "
                "'{0}' during {1}s !\n".format(command, timeout))
            logger.debug(no_ec_msg)

            raise TimeoutError(
                no_ec_msg + output_tmpl.format(
                    result.stdout_brief,
                    result.stderr_brief
                ))
