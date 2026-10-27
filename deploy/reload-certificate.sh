#!/bin/sh
set -eu
# Template: replace briefing.example.com with the domain in nginx-briefing.conf.
case " ${RENEWED_DOMAINS:-} " in
  *" briefing.example.com "*)
    /usr/bin/systemctl reload nginx-briefing.service
    ;;
esac
