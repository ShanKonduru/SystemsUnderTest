"""python -m insurance_copilot.mcp_server            stdio, one customer (COPILOT_CUSTOMER_ID), started by the app
python -m insurance_copilot.mcp_server --http PORT  streamable HTTP, every customer (X-Customer-Id header)"""

import argparse

parser = argparse.ArgumentParser(prog="python -m insurance_copilot.mcp_server", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
parser.add_argument("--http", type=int, metavar="PORT", help="serve over streamable HTTP on this port")
parser.add_argument("--host", default="127.0.0.1", help="HTTP mode: address to bind (default 127.0.0.1)")
args = parser.parse_args()

if args.http:
    from insurance_copilot.mcp_server.http import serve

    serve(args.http, args.host)
else:
    from insurance_copilot.mcp_server.server import main

    main()
