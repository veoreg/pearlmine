# SPDX-License-Identifier: Apache-2.0
"""Standalone entry. Child sensors and workers re-enter through `-m boris_pearl.*`; nothing else is runnable."""
import runpy
import sys


def main():
    args=sys.argv[1:]
    if len(args)>=2 and args[0]=="-m":
        if not args[1].startswith("boris_pearl."):raise SystemExit("Only PearlMine modules can run here.")
        sys.argv=[args[1],*args[2:]]
        runpy.run_module(args[1],run_name="__main__",alter_sys=True)
    elif args==["--agent"]:
        from boris_pearl.agent_ui import main as agent
        agent()
    else:
        from boris_pearl.dashboard import run
        run()


if __name__=="__main__":main()
