#!/bin/bash
cd /media/nfs/development/GIT/pfintchk
export WORKON_HOME=~/.virtualenvs
source /usr/bin/virtualenvwrapper.sh
# behind nginx
./pfintchk.py &> /media/cifs/dokumente/g3logs/pfintchk.log

