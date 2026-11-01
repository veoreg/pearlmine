# SPDX-License-Identifier: Apache-2.0
"""Manually started HTTPS agent; no install/service/autostart or real mining."""
import argparse
import ipaddress
import json
from pathlib import Path
from .control_center import DeviceService
from .remote_control import Pairing,ControlState,initialize,make_server


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command",choices=("init","serve"))
    parser.add_argument("--directory",type=Path,default=Path("state/agent"))
    parser.add_argument("--name",default="Мой компьютер")
    parser.add_argument("--url",default="https://127.0.0.1:8431")
    parser.add_argument("--listen",default="127.0.0.1",help="Explicit local/VPN interface IP; never a wildcard/public interface")
    parser.add_argument("--port",type=int,default=8431)
    parser.add_argument("--allow-tests",action="store_true",help="Explicitly permit bounded5..300s artificial GPU tests; real mining never supported")
    args=parser.parse_args()
    try:
        if args.command=="init":
            initialize(args.directory,args.name,args.url)
            print("Код подключения сохранён в "+str(args.directory/"pairing.json")+". Файл даёт управление агентом: передавайте только своей панели.")
            return
        address=ipaddress.ip_address(args.listen)
        if address.is_unspecified or address.is_multicast or address.is_global:
            raise ValueError("Укажите конкретный локальный/VPN IP. Публикация агента на публичном интерфейсе отключена.")
        if not 1<=args.port<=65535:raise ValueError("Неверный порт.")
        pairing=Pairing.parse((args.directory/"pairing.json").read_text(encoding="utf-8"))
        service=DeviceService();state=ControlState(service,pairing.token,allow_tests=args.allow_tests)
        server=None
        try:
            server=make_server((args.listen,args.port),args.directory/"agent-cert.pem",args.directory/"agent-key.pem",state)
            print(f"Агент готов на {args.listen}:{args.port}; запуск тестов: {args.allow_tests}. Закрытие останавливает работу; это не служба.")
            server.serve_forever(poll_interval=.2)
        except KeyboardInterrupt:pass
        finally:
            if server:server.server_close()
            state.close()
            for thread in service.threads:thread.join(timeout=5)
    except (ValueError,OSError,ImportError) as exc:parser.error(str(exc))


if __name__=="__main__":main()
