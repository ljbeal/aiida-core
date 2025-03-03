# -*- coding: utf-8 -*-
###########################################################################
# Copyright (c), The AiiDA team. All rights reserved.                     #
# This file is part of the AiiDA code.                                    #
#                                                                         #
# The code is hosted on GitHub at https://github.com/aiidateam/aiida-core #
# For further information on the license, see the LICENSE.txt file        #
# For further information please visit http://www.aiida.net               #
###########################################################################
# pylint: disable=too-many-arguments
"""`verdi process` command."""
import click

from aiida.cmdline.commands.cmd_verdi import verdi
from aiida.cmdline.params import arguments, options, types
from aiida.cmdline.utils import decorators, echo
from aiida.common.log import LOG_LEVELS
from aiida.manage import get_manager
from aiida.orm import ProcessNode, QueryBuilder


def valid_projections():
    """Return list of valid projections for the ``--project`` option of ``verdi process list``.

    This indirection is necessary to prevent loading the imported module which slows down tab-completion.
    """
    from aiida.tools.query.calculation import CalculationQueryBuilder
    return CalculationQueryBuilder.valid_projections


def default_projections():
    """Return list of default projections for the ``--project`` option of ``verdi process list``.

    This indirection is necessary to prevent loading the imported module which slows down tab-completion.
    """
    from aiida.tools.query.calculation import CalculationQueryBuilder
    return CalculationQueryBuilder.default_projections


@verdi.group('process')
def verdi_process():
    """Inspect and manage processes."""


@verdi_process.command('list')
@options.PROJECT(type=types.LazyChoice(valid_projections), default=lambda: default_projections())  # pylint: disable=unnecessary-lambda
@options.ORDER_BY()
@options.ORDER_DIRECTION()
@options.GROUP(help='Only include entries that are a member of this group.')
@options.ALL(help='Show all entries, regardless of their process state.')
@options.PROCESS_STATE()
@options.PROCESS_LABEL()
@options.PAUSED()
@options.EXIT_STATUS()
@options.FAILED()
@options.PAST_DAYS()
@options.LIMIT()
@options.RAW()
@click.pass_context
@decorators.with_dbenv()
def process_list(
    ctx, all_entries, group, process_state, process_label, paused, exit_status, failed, past_days, limit, project, raw,
    order_by, order_dir
):
    """Show a list of running or terminated processes.

    By default, only those that are still running are shown, but there are options to show also the finished ones.
    """
    # pylint: disable=too-many-locals
    from tabulate import tabulate

    from aiida.cmdline.commands.cmd_daemon import execute_client_command
    from aiida.cmdline.utils.common import print_last_process_state_change
    from aiida.engine.daemon.client import get_daemon_client
    from aiida.orm import ProcessNode, QueryBuilder
    from aiida.tools.query.calculation import CalculationQueryBuilder

    relationships = {}

    if group:
        relationships['with_node'] = group

    builder = CalculationQueryBuilder()
    filters = builder.get_filters(all_entries, process_state, process_label, paused, exit_status, failed)
    query_set = builder.get_query_set(
        relationships=relationships, filters=filters, order_by={order_by: order_dir}, past_days=past_days, limit=limit
    )
    projected = builder.get_projected(query_set, projections=project)
    headers = projected.pop(0)

    if raw:
        tabulated = tabulate(projected, tablefmt='plain')
        echo.echo(tabulated)
        return

    tabulated = tabulate(projected, headers=headers)
    echo.echo(tabulated)
    echo.echo(f'\nTotal results: {len(projected)}\n')

    if 'cached' in project:
        echo.echo_report('\u267B Processes marked with check-mark were not run but taken from the cache.')
        echo.echo_report('Add the option `-P pk cached_from` to the command to display cache source.')

    print_last_process_state_change()

    if not get_daemon_client().is_daemon_running:
        echo.echo_warning('The daemon is not running', bold=True)
        return

    echo.echo_report('Checking daemon load... ', nl=False)
    response = execute_client_command('get_numprocesses')

    if not response:
        # Daemon could not be reached
        return

    try:
        active_workers = response['numprocesses']
    except KeyError:
        echo.echo_report('No active daemon workers.')
    else:
        # Second query to get active process count. Currently this is slow but will be fixed with issue #2770. It is
        # placed at the end of the command so that the user can Ctrl+C after getting the process table.
        slots_per_worker = ctx.obj.config.get_option('daemon.worker_process_slots', scope=ctx.obj.profile.name)
        active_processes = QueryBuilder().append(
            ProcessNode, filters={
                'attributes.process_state': {
                    'in': ('created', 'waiting', 'running')
                }
            }
        ).count()
        available_slots = active_workers * slots_per_worker
        percent_load = active_processes / available_slots
        if percent_load > 0.9:  # 90%
            echo.echo_warning(f'{percent_load * 100:.0f}% of the available daemon worker slots have been used!')
            echo.echo_warning('Increase the number of workers with `verdi daemon incr`.')
        else:
            echo.echo_report(f'Using {percent_load * 100:.0f}% of the available daemon worker slots.')


@verdi_process.command('show')
@arguments.PROCESSES()
@decorators.with_dbenv()
def process_show(processes):
    """Show details for one or multiple processes."""
    from aiida.cmdline.utils.common import get_node_info

    for process in processes:
        echo.echo(get_node_info(process))


@verdi_process.command('call-root')
@arguments.PROCESSES()
@decorators.with_dbenv()
def process_call_root(processes):
    """Show root process of the call stack for the given processes."""
    for process in processes:

        caller = process.caller

        if caller is None:
            echo.echo(f'No callers found for Process<{process.pk}>')
            continue

        while True:
            next_caller = caller.caller

            if next_caller is None:
                break

            caller = next_caller

        echo.echo(f'{caller.pk}')


@verdi_process.command('report')
@arguments.PROCESSES()
@click.option('-i', '--indent-size', type=int, default=2, help='Set the number of spaces to indent each level by.')
@click.option(
    '-l',
    '--levelname',
    type=click.Choice(list(LOG_LEVELS)),
    default='REPORT',
    help='Filter the results by name of the log level.'
)
@click.option(
    '-m', '--max-depth', 'max_depth', type=int, default=None, help='Limit the number of levels to be printed.'
)
@click.option(
    '-M',
    '--most-recent-node',
    'most_recent_node',
    is_flag=True,
    default=False,
    help='Select the most recently created process node.'
)
@decorators.with_dbenv()
def process_report(processes, levelname, indent_size, max_depth, most_recent_node):
    """Show the log report for one or multiple processes."""
    from aiida.cmdline.utils.common import get_calcjob_report, get_process_function_report, get_workchain_report
    from aiida.orm import CalcFunctionNode, CalcJobNode, WorkChainNode, WorkFunctionNode, load_node
    from aiida.tools.query.calculation import CalculationQueryBuilder

    if most_recent_node:
        builder = QueryBuilder().append(ProcessNode, tag='n').order_by({'n': {'ctime': 'desc'}})
        processes = [builder.first(flat=True)]

        echo.echo_info(f'Showing results for most recent node {processes[0]}')

        # try:
        #     remote_dir = node.outputs.remote_folder.get_attribute('remote_path')
        #     echo.echo(f'Remote Directory: {remote_dir}')
        # except AttributeError:
        #     echo.echo(f'No Remote Directory Found')

    for process in processes:
        if isinstance(process, CalcJobNode):
            echo.echo(get_calcjob_report(process))
        elif isinstance(process, WorkChainNode):
            echo.echo(get_workchain_report(process, levelname, indent_size, max_depth))
        elif isinstance(process, (CalcFunctionNode, WorkFunctionNode)):
            echo.echo(get_process_function_report(process))
        else:
            echo.echo(f'Nothing to show for node type {process.__class__}')


@verdi_process.command('status')
@click.option('-c', '--call-link-label', 'call_link_label', is_flag=True, help='Include the call link label if set.')
@click.option(
    '-m', '--max-depth', 'max_depth', type=int, default=None, help='Limit the number of levels to be printed.'
)
@arguments.PROCESSES()
def process_status(call_link_label, max_depth, processes):
    """Print the status of one or multiple processes."""
    from aiida.cmdline.utils.ascii_vis import format_call_graph

    for process in processes:
        graph = format_call_graph(process, max_depth=max_depth, call_link_label=call_link_label)
        echo.echo(graph)


@verdi_process.command('kill')
@arguments.PROCESSES()
@options.ALL(help='Kill all processes if no specific processes are specified.')
@options.TIMEOUT()
@options.WAIT()
@decorators.with_dbenv()
def process_kill(processes, all_entries, timeout, wait):
    """Kill running processes."""
    from aiida.engine.processes import control

    if processes and all_entries:
        raise click.BadOptionUsage('all', 'cannot specify individual processes and the `--all` flag at the same time.')

    if all_entries:
        click.confirm('Are you sure you want to kill all processes?', abort=True)

    try:
        message = 'Killed through `verdi process kill`'
        control.kill_processes(processes, all_entries=all_entries, timeout=timeout, wait=wait, message=message)
    except control.ProcessTimeoutException as exception:
        echo.echo_critical(str(exception) + '\nFrom the CLI you can call `verdi devel revive <PID>`.')


@verdi_process.command('pause')
@arguments.PROCESSES()
@options.ALL(help='Pause all active processes if no specific processes are specified.')
@options.TIMEOUT()
@options.WAIT()
@decorators.with_dbenv()
def process_pause(processes, all_entries, timeout, wait):
    """Pause running processes."""
    from aiida.engine.processes import control

    if processes and all_entries:
        raise click.BadOptionUsage('all', 'cannot specify individual processes and the `--all` flag at the same time.')

    try:
        message = 'Paused through `verdi process pause`'
        control.pause_processes(processes, all_entries=all_entries, timeout=timeout, wait=wait, message=message)
    except control.ProcessTimeoutException as exception:
        echo.echo_critical(str(exception) + '\nFrom the CLI you can call `verdi devel revive <PID>`.')


@verdi_process.command('play')
@arguments.PROCESSES()
@options.ALL(help='Play all paused processes if no specific processes are specified.')
@options.TIMEOUT()
@options.WAIT()
@decorators.with_dbenv()
def process_play(processes, all_entries, timeout, wait):
    """Play (unpause) paused processes."""
    from aiida.engine.processes import control

    if processes and all_entries:
        raise click.BadOptionUsage('all', 'cannot specify individual processes and the `--all` flag at the same time.')

    try:
        control.play_processes(processes, all_entries=all_entries, timeout=timeout, wait=wait)
    except control.ProcessTimeoutException as exception:
        echo.echo_critical(str(exception) + '\nFrom the CLI you can call `verdi devel revive <PID>`.')


@verdi_process.command('watch')
@arguments.PROCESSES()
@decorators.with_dbenv()
@decorators.only_if_daemon_running(echo.echo_warning, 'daemon is not running, so process may not be reachable')
def process_watch(processes):
    """Watch the state transitions for a process."""
    from time import sleep

    from kiwipy import BroadcastFilter

    def _print(communicator, body, sender, subject, correlation_id):  # pylint: disable=unused-argument
        """Format the incoming broadcast data into a message and echo it to stdout."""
        if body is None:
            body = 'No message specified'

        if correlation_id is None:
            correlation_id = '--'

        echo.echo(f'Process<{sender}> [{subject}|{correlation_id}]: {body}')

    communicator = get_manager().get_communicator()
    echo.echo_report('watching for broadcasted messages, press CTRL+C to stop...')

    for process in processes:

        if process.is_terminated:
            echo.echo_error(f'Process<{process.pk}> is already terminated')
            continue

        communicator.add_broadcast_subscriber(BroadcastFilter(_print, sender=process.pk))

    try:
        # Block this thread indefinitely until interrupt
        while True:
            sleep(2)
    except (SystemExit, KeyboardInterrupt):
        echo.echo('')  # add a new line after the interrupt character
        echo.echo_report('received interrupt, exiting...')
        try:
            communicator.close()
        except RuntimeError:
            pass

        # Reraise to trigger clicks builtin abort sequence
        raise
