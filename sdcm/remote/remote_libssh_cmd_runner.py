# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
#
# See LICENSE for more details.
#
# Copyright (c) 2020 ScyllaDB

from typing import Optional, List
import os
import time
import threading
import socket

from invoke.watchers import StreamWatcher

from sdcm.utils.decorators import retrying

from .libssh2_client import Client as LibSSH2Client, Timings
from .libssh2_client.exceptions import AuthenticationException, UnknownHostException, ConnectError, \
    FailedToReadCommandOutput, CommandTimedOut, FailedToRunCommand, OpenChannelTimeout, SocketRecvError, \
    UnexpectedExit, Failure
from .libssh2_client.result import Result
from .base import RetryableNetworkException
from .remote_base import RemoteCmdRunnerBase


class RemoteLibSSH2CmdRunner(RemoteCmdRunnerBase, ssh_transport='libssh2'):  # pylint: disable=too-many-instance-attributes
    """Remoter that mimic RemoteCmdRunner, under the hood it runs libssh2 client, instead of paramiko
    Main problem in libssh2 - is that it is not thread safe, we mitigate this problem by having
      _connection_thread_map - a dictionary in which we bind thread to the libssh2 session.
    Whenever remoter read self.connection, we return value from _connection_thread_map associated with current thread,
      And if it is not there, we create it.
    """
    connection: LibSSH2Client
    exception_unexpected = UnexpectedExit
    exception_failure = Failure
    exception_retryable = (
        # Exceptions that are not signaling about
        AuthenticationException, UnknownHostException, ConnectError, FailedToReadCommandOutput,
        CommandTimedOut, FailedToRunCommand, OpenChannelTimeout, SocketRecvError, socket.timeout
    )

    def _create_connection(self) -> LibSSH2Client:
        return LibSSH2Client(
            host=self.hostname,
            user=self.user,
            port=self.port,
            pkey=os.path.expanduser(self.key_file),
            timings=Timings(keepalive_timeout=0, connect_timeout=self.connect_timeout)
        )

    def is_up(self, timeout: float = 30) -> bool:
        end_time = time.perf_counter() + timeout
        while time.perf_counter() <= end_time:
            try:
                if self.connection.check_if_alive(timeout):
                    return True
            except:  # pylint: disable=bare-except
                try:
                    self.connection.close()
                    self.connection.open(timeout)
                except:  # pylint: disable=bare-except
                    pass
        return False

    def _run_on_retryable_exception(self, exc: Exception, new_session: bool) -> bool:
        self.log.error(exc)
        if isinstance(exc, FailedToRunCommand) and not new_session:
            self.log.debug('Reestablish the session...')
            try:
                self.connection.disconnect()
            except:  # pylint: disable=bare-except
                pass
            try:
                self.connection.connect()
            except:  # pylint: disable=bare-except
                pass
        if self._is_error_retryable(str(exc)) or isinstance(exc, self.exception_retryable):
            raise RetryableNetworkException(str(exc), original=exc)
        return True
