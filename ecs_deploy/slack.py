import requests
from os import getenv
from slacker import Slacker


class SlackException(Exception):
    pass


class SlackDeploymentException(SlackException):
    pass


class SlackWebhookLogger:

    def __init__(self):
        self.slack_webhook_endpoint = getenv('SLACK_WEBHOOK_ENDPOINT')

    def post_to_slack(self, message, attachments):
        from urllib import request, parse
        import json

        post = {"text": "{0}".format(message)}

        try:
            json_data = json.dumps(post)
            req = request.Request(self.slack_webhook_endpoint,
                                  data=json_data.encode('ascii'),
                                  headers={'Content-Type': 'application/json'})
            resp = request.urlopen(req)
            print("Post to slack webhook response: " + str(resp))
        except Exception as em:
            print("EXCEPTION: " + str(em))


class SlackLogger(object):

    def __init__(self):
        self.muted = getenv('SLACK_MUTED', False)
        if getenv('SLACK_TOKEN', None) is not None:
            print('Initializing Slack Token based client')
            self.slack = Slacker(getenv('SLACK_TOKEN'))
            self.slack_webhook_endpoint = None
        elif getenv('SLACK_WEBHOOK_ENDPOINT', None) is not None:
            print('Initializing Slack Webhook based client')
            self.slack_webhook_endpoint = SlackWebhookLogger()
            self.slack = None
            print('slack webhook endpoint: ' + str(self.slack_webhook_endpoint))
        else:
            self.slack = None
            self.slack_webhook_endpoint = None

        self.channel = getenv('SLACK_CHANNEL', "test")

    def progress_bar(self, running, pending, desired):
        progress = round(float(running) * 100 / float(desired) / 5)
        pending = round(float(pending) * 100 / float(desired) / 5)
        return (progress * chr(9608)) + (pending * chr(9618)) + ((20 - progress - pending) * chr(9617))

    def post_to_slack(self, message, attachments, chat_update=None):
        '''
            If chat_update is a chat object, this function will update that object
            with the new text. Callers interested in updating chats can save the 
            return value and send it in chat_update the next time.
        '''
        if self.muted:
            return
        if self.slack is not None:
            if chat_update is not None:
                res = self.slack.chat.update(chat_update['channel'], text=message, attachments=attachments, as_user=True, ts=chat_update['ts'])
            else:
                res = self.slack.chat.post_message(self.channel, text=message, attachments=attachments, as_user=True)
            return res.body 
        elif self.slack_webhook_endpoint is not None:
            self.slack_webhook_endpoint.post_to_slack(message, attachments)
        else:
            raise Exception('SLACK_TOKEN (or) SLACK_WEBHOOK_ENDPOINT should to be specified!')

    def service_url(self, cluster, service):
        return "https://us-west-2.console.aws.amazon.com/ecs/home?region=us-west-2#/clusters/%s/services/%s/deployments" % (cluster, service)

    def cluster_url(self, cluster):
        return "https://us-west-2.console.aws.amazon.com/ecs/home?region=us-west-2#/clusters/%s" % cluster

    def get_deploy_start_payload(self, service, task_definition):
        #import pdb;pdb.set_trace()
        service_link = self.service_url(service.cluster, service.name)
        cluster_link = self.cluster_url(service.cluster)
        return "Deploying service <%s|%s> / <%s|%s> \n_Image: %s_" % (cluster_link, service.cluster, service_link, service.name, ",".join( [c['image'] for c in task_definition.containers]) )

    def get_deploy_progress_payload(self, service, task_definition):
        cluster_link = self.cluster_url(service.cluster)
        service_link = self.service_url(service.cluster, service.name)
        primary = [dep for dep in service['deployments'] if dep['status']=='PRIMARY'][0]
        run = primary['runningCount']
        pend = primary['pendingCount']
        des = primary['desiredCount']
        primary_message = {
        "title": f'PRIMARY <{cluster_link}|{service.cluster}> / <{service_link}|{service.name}>',
        "text": self.progress_bar(run, pend, des) + "\tRunning: %s Pending: %s  Desired: %s" % (run, pend, des)
        }
        attachments = [primary_message]

        active = [dep for dep in service['deployments'] if dep['status']=='ACTIVE']
        for act in active:
            run = act['runningCount']
            pend = act['pendingCount']
            des = act['desiredCount']

            attachments.append({
              "title": "ACTIVE",
              "text": self.progress_bar(run, pend, des) + "\tRunning: %s Pending: %s  Desired: %s" % (run, pend, des)
            })

        messg = self.get_deploy_start_payload(service, task_definition)
        return '', attachments

    def get_deploy_finish_payload(self, service, task_definition):
        primary = [dep for dep in service['deployments'] if dep['status']=='PRIMARY'][0]
        run = primary['runningCount']
        pend = primary['pendingCount']
        des = primary['desiredCount']

        primary_message = {
            "title": "Deploy finished!",
            "color": "#7CD197",
            "text": "Running: %s Pending: %s  Desired: %s" % (run, pend, des)
            }
        attachments = [primary_message]

        service_link = self.service_url(service.cluster, service.name)
        cluster_link = self.cluster_url(service.cluster)

        messg = "Deploy finished: <%s|%s> / <%s|%s>\n_Image: %s_" % (cluster_link, service.cluster, service_link, service.name, ",".join( [c['image'] for c in task_definition.containers]))
        return messg, attachments

    def log_deploy_start(self, service, task_definition):
        message = self.get_deploy_start_payload(service, task_definition)
        self.post_to_slack(message, None)

    def log_deploy_progress(self, service, task_definition, chat_update):
        primary = [dep for dep in service['deployments'] if dep['status']=='PRIMARY'][0]
        des = primary['desiredCount']
        if des <= 0:
            print('Desired Count of this service is Zero! Skipping Progress Bar Message Generation!')
            return

        message, attachments = self.get_deploy_progress_payload(service, task_definition)
        if self.slack is not None:
            return self.post_to_slack(message, attachments, chat_update)
        else:
            print('Posting Deploy Progress to Slack Channel in Webhook Mode - Yet to be implemented! Waiting for Deploy to Complete!')

    def log_deploy_finish(self, service, task_definition):
        message, attachments = self.get_deploy_finish_payload(service, task_definition)
        self.post_to_slack(message, attachments)
