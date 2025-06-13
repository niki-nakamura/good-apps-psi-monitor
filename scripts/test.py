curl -F file=@/etc/hosts \
     -F channels=<チャンネルID> \
     -H "Authorization: Bearer xoxb-***" \
     https://slack.com/api/files.upload
