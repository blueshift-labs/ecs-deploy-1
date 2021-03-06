from __future__ import print_function, absolute_import

from os import getenv
from time import sleep

import queue
import threading
import traceback
import time
import random

import copy
import click
import getpass
from datetime import datetime, timedelta

from ecs_deploy import VERSION
from ecs_deploy.ecs import DeployAction, ScaleAction, RunAction, EcsClient, \
    TaskPlacementError, EcsError
from ecs_deploy.slack import SlackLogger, SlackException

SLACK_LOGGER = SlackLogger()

@click.group()
@click.version_option(version=VERSION, prog_name='ecs-deploy')
def ecs():  # pragma: no cover
    pass


def get_client(access_key_id, secret_access_key, region, profile):
    return EcsClient(access_key_id, secret_access_key, region, profile)


@click.command()
@click.option('--cluster', required=True)
@click.option('--services', required=True, help='Comma separated list of services')
@click.option('-i', '--image', type=(str, str), multiple=True, help='Overwrites the image for a container: <container> <image>')
@click.option('--timeout', required=False, default=900, type=int, help='Amount of seconds to wait for deployment before command fails (default: 900)')
@click.option('--worker_count', required=False, default=16, type=int, help='Number of worker threads to run')
@click.option('--ignore-warnings', is_flag=True, help='Do not fail deployment on warnings (port already in use or insufficient memory/CPU)')
@click.option('--force-new-deployment/--no-force-new-deployment', default=False, help='Recycle containers')
@click.pass_context
def deploy_many(ctx, cluster, services, **kwargs):
    """
    Redeploy/modify many services in parallel.
    Services must be of the same cluster.
    This command calls the `deploy` command for every service in the list.
    """
    slist = services.split(',')
    click.secho(f'Deploying to cluster={cluster} services={slist} args={kwargs}')
    num_worker_threads = kwargs['worker_count']
    del kwargs['worker_count']

    def worker(q, tid):
        # Before starting, sleep a random duration to avoid hitting rate limits
        time.sleep(random.randint(1,15))
        while True:
            item = q.get()
            if item is None:
                break
            cluster, service = item
            service = service.strip()
            click.secho(f'Starting deploy cluster={cluster} service={service} tid={tid}')
            try:
                ctx.invoke(deploy, cluster=cluster, service=service, **kwargs)
            except Exception as e:
                tb = traceback.format_exc()
                click.secho(f'Got error `{e}` for {service} tid={tid} \n {tb}')
            finally:
                q.task_done()
            click.secho(f'Done deploy cluster={cluster} service={service} tid={tid}')

    q = queue.Queue()
    threads = []
    for i in range(num_worker_threads):
        t = threading.Thread(target=worker, args=(q, i))
        t.start()
        threads.append(t)

    for service in slist:
        service = service.strip()
        if service:
            q.put((cluster, service))

    # block until all tasks are done
    q.join()

    # stop workers
    for i in range(num_worker_threads):
        q.put(None)
    for t in threads:
        t.join()


@click.command()
@click.option('--cluster', required=True)
@click.option('--service', required=True)
@click.option('-t', '--tag', help='Changes the tag for ALL container images')
@click.option('-i', '--image', type=(str, str), multiple=True, help='Overwrites the image for a container: <container> <image>')
@click.option('-c', '--command', type=(str, str), multiple=True, help='Overwrites the command in a container: <container> <command>')
@click.option('-e', '--env', type=(str, str, str), multiple=True, help='Adds or changes an environment variable: <container> <name> <value>')
@click.option('-r', '--role', type=str, help='Sets the task\'s role ARN: <task role ARN>')
@click.option('--task', type=str, help='Task definition to be deployed. Can be a task ARN or a task family with optional revision')
@click.option('--region', required=False, help='AWS region (e.g. eu-central-1)')
@click.option('--access-key-id', required=False, help='AWS access key id')
@click.option('--secret-access-key', required=False, help='AWS secret access key')
@click.option('--profile', required=False, help='AWS configuration profile name')
@click.option('--timeout', required=False, default=900, type=int, help='Amount of seconds to wait for deployment before command fails (default: 900)')
@click.option('--ignore-warnings', is_flag=True, help='Do not fail deployment on warnings (port already in use or insufficient memory/CPU)')
@click.option('--newrelic-apikey', required=False, help='New Relic API Key for recording the deployment')
@click.option('--newrelic-appid', required=False, help='New Relic App ID for recording the deployment')
@click.option('--comment', required=False, help='Description/comment for recording the deployment')
@click.option('--user', required=False, help='User who executes the deployment (used for recording)')
@click.option('--diff/--no-diff', default=True, help='Print which values were changed in the task definition (default: --diff)')
@click.option('--deregister/--no-deregister', default=False, help='Deregister or keep the old task definition (default: --deregister)')
@click.option('--rollback/--no-rollback', default=False, help='Rollback to previous revision, if deployment failed (default: --no-rollback)')
@click.option('--force-new-deployment/--no-force-new-deployment', default=False, help='Recycle containers')
def deploy(cluster, service, tag, image, command, env, role, task, region, access_key_id, secret_access_key, profile, timeout, newrelic_apikey, newrelic_appid, comment, user, ignore_warnings, diff, deregister, rollback, force_new_deployment):
    """
    Redeploy or modify a service.

    \b
    CLUSTER is the name of your cluster (e.g. 'my-custer') within ECS.
    SERVICE is the name of your service (e.g. 'my-app') within ECS.

    When not giving any other options, the task definition will not be changed.
    It will just be duplicated, so that all container images will be pulled
    and redeployed.
    """

    try:
        client = get_client(access_key_id, secret_access_key, region, profile)
        deployment = DeployAction(client, cluster, service)

        td = get_task_definition(deployment, task)
        new_td = copy.deepcopy(td) # Make a copy if nothing need to be updated.

        td.set_images(tag, **{key: value for (key, value) in image})
        td.set_commands(**{key: value for (key, value) in command})
        td.set_environment(env)
        td.set_role_arn(role)

        if td.diff != []:
            print_diff(td)
            new_td = create_task_definition(deployment, td)

        try:
            deploy_task_definition(
                deployment=deployment,
                task_definition=new_td,
                title='Deploying new task definition',
                success_message='Deployment successful',
                failure_message='Deployment failed',
                timeout=timeout,
                deregister=deregister,
                previous_task_definition=td,
                ignore_warnings=ignore_warnings,
                force_new_deployment=force_new_deployment,
            )

        except TaskPlacementError as e:
            if rollback:
                click.secho('%s\n' % str(e), fg='red')
                rollback_task_definition(deployment, td, new_td)
                exit(1)
            else:
                raise

        record_deployment(tag, newrelic_apikey, newrelic_appid, comment, user)

    except (EcsError, SlackException) as e:
        click.secho('%s\n' % str(e), fg='red')
        exit(1)


@click.command()
@click.argument('cluster')
@click.argument('service')
@click.argument('desired_count', type=int)
@click.option('--region', help='AWS region (e.g. eu-central-1)')
@click.option('--access-key-id', help='AWS access key id')
@click.option('--secret-access-key', help='AWS secret access key')
@click.option('--profile', help='AWS configuration profile name')
@click.option('--timeout', default=900, type=int, help='AWS configuration profile')
@click.option('--ignore-warnings', is_flag=True, help='Do not fail deployment on warnings (port already in use or insufficient memory/CPU)')
def scale(cluster, service, desired_count, access_key_id, secret_access_key, region, profile, timeout, ignore_warnings):
    """
    Scale a service up or down.

    \b
    CLUSTER is the name of your cluster (e.g. 'my-custer') within ECS.
    SERVICE is the name of your service (e.g. 'my-app') within ECS.
    DESIRED_COUNT is the number of tasks your service should run.
    """
    try:
        client = get_client(access_key_id, secret_access_key, region, profile)
        scaling = ScaleAction(client, cluster, service)
        click.secho('Updating service')
        scaling.scale(desired_count)
        click.secho(
            'Successfully changed desired count to: %s\n' % desired_count,
            fg='green'
        )
        wait_for_finish(
            action=scaling,
            timeout=timeout,
            title='Scaling service',
            success_message='Scaling successful',
            failure_message='Scaling failed',
            ignore_warnings=ignore_warnings
        )

    except EcsError as e:
        click.secho('%s\n' % str(e), fg='red')
        exit(1)


def wait_for_finish(action, timeout, title, success_message, failure_message,
                    ignore_warnings, task_definition=None):
    click.secho(title, nl=False)
    waiting = True
    waiting_timeout = datetime.now() + timedelta(seconds=timeout)
    service = action.get_service()
    inspected_until = None

    chat_update = SLACK_LOGGER.log_deploy_progress(service, task_definition, None)
    while waiting and datetime.now() < waiting_timeout:
        click.secho('.', nl=False)
        service = action.get_service()
        inspected_until = inspect_errors(
            service=service,
            failure_message=failure_message,
            ignore_warnings=ignore_warnings,
            since=inspected_until,
            timeout=False
        )
        waiting = not action.is_deployed(service)
        chat_update = SLACK_LOGGER.log_deploy_progress(service, task_definition, chat_update)

        if waiting:
            sleep(30)

    inspect_errors(
        service=service,
        failure_message=failure_message,
        ignore_warnings=ignore_warnings,
        since=inspected_until,
        timeout=waiting
    )

    click.secho('\n%s\n' % success_message, fg='green')


def deploy_task_definition(deployment, task_definition, title, success_message,
                           failure_message, timeout, deregister,
                           previous_task_definition, ignore_warnings, force_new_deployment=False):
    click.secho('Updating service')
    SLACK_LOGGER.log_deploy_start(deployment.service, task_definition)
    deployment.deploy(task_definition, force_new_deployment=force_new_deployment)

    message = 'Successfully changed task definition to: %s:%s\n' % (
        task_definition.family,
        task_definition.revision
    )

    click.secho(message, fg='green')

    wait_for_finish(
        action=deployment,
        task_definition=task_definition,
        timeout=timeout,
        title=title,
        success_message=success_message,
        failure_message=failure_message,
        ignore_warnings=ignore_warnings,
    )

    SLACK_LOGGER.log_deploy_finish(deployment.service, task_definition)
    if deregister:
        deregister_task_definition(deployment, previous_task_definition)


def get_task_definition(action, task):
    if task:
        task_definition = action.get_task_definition(task)
    else:
        task_definition = action.get_current_task_definition(action.service)
        task = task_definition.family_revision

    click.secho('Deploying based on task definition: %s\n' % task)

    return task_definition


def create_task_definition(action, task_definition):
    click.secho('Creating new task definition revision')
    new_td = action.update_task_definition(task_definition)

    click.secho(
        'Successfully created revision: %d\n' % new_td.revision,
        fg='green'
    )

    return new_td


def deregister_task_definition(action, task_definition):
    click.secho('Deregister task definition revision')
    action.deregister_task_definition(task_definition)
    click.secho(
        'Successfully deregistered revision: %d\n' % task_definition.revision,
        fg='green'
    )


def rollback_task_definition(deployment, old, new, timeout=900):
    click.secho(
        'Rolling back to task definition: %s\n' % old.family_revision,
        fg='yellow',
    )
    deploy_task_definition(
        deployment=deployment,
        task_definition=old,
        title='Deploying previous task definition',
        success_message='Rollback successful',
        failure_message='Rollback failed. Please check ECS Console',
        timeout=timeout,
        deregister=True,
        previous_task_definition=new,
        ignore_warnings=False,
    )
    click.secho(
        'Deployment failed, but service has been rolled back to previous '
        'task definition: %s\n' % old.family_revision, fg='yellow')


def record_deployment(revision, api_key, app_id, comment, user):
    # api_key = getenv('NEW_RELIC_API_KEY', api_key)
    # app_id = getenv('NEW_RELIC_APP_ID', app_id)

    # if not revision or not api_key or not app_id:
    #     return False

    #user = user or getpass.getuser()
    #click.secho('Recording deployment in New Relic', nl=False)
    #deployment = SlackLogger()
    #deployment.deploy(revision, '', comment)

    click.secho('\nDone\n')
    return True


def print_diff(task_definition, title='Updating task definition'):
    if task_definition.diff:
        click.secho(title)
        for diff in task_definition.diff:
            click.secho(str(diff), fg='blue')
        click.secho('')


def inspect_errors(service, failure_message, ignore_warnings, since, timeout):
    error = False
    last_error_timestamp = since

    warnings = service.get_warnings(since)
    for timestamp in warnings:
        message = warnings[timestamp]
        click.secho('')
        if ignore_warnings:
            last_error_timestamp = timestamp
            click.secho('%s\nWARNING: %s' % (timestamp, message))
            click.secho('Continuing.', nl=False)
        else:
            click.secho('%s\nERROR: %s\n' % (timestamp, message))
            error = True

    if service.older_errors:
        click.secho('')
        click.secho('Older errors')
        for timestamp in service.older_errors:
            click.secho('%s\n%s\n' % (timestamp, service.older_errors[timestamp]))

    if timeout:
        error = True
        failure_message += ' due to timeout. Please see: ' \
                           'https://github.com/fabfuel/ecs-deploy#timeout'
        click.secho('')

    if error:
        raise TaskPlacementError(failure_message)

    return last_error_timestamp


ecs.add_command(deploy)
ecs.add_command(deploy_many)
ecs.add_command(scale)

if __name__ == '__main__':  # pragma: no cover
    ecs()
