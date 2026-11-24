"""Prepare the mounted database directory, then run the command as appuser."""
import os
import pwd
import sys


def main():
    if os.geteuid() == 0:
        account = pwd.getpwnam("appuser")
        os.chown("/app/data", account.pw_uid, account.pw_gid)
        os.setgroups([])
        os.setgid(account.pw_gid)
        os.setuid(account.pw_uid)
    command = sys.argv[1:]
    os.execvp(command[0], command)


if __name__ == "__main__":
    main()
