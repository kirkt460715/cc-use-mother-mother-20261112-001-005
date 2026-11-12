Community service accessibility analysis. Python 3.11+ standard library, SQLite. All fixture data are synthetic.
Run: python accessibility.py demo --fixture fixtures/community.json
Test: python -m unittest discover -s tests -v
Build: python -m compileall -q network catchments supply equity analysis_runs accessibility.py
Analyze: python accessibility.py analyze --input fixtures/community.json --direction outbound
Archive: python accessibility.py create --database /tmp/access.sqlite3 --branch baseline --input fixtures/community.json
Compute: python accessibility.py run --database /tmp/access.sqlite3 --branch baseline --revision 1
Graph edges are directed. Pool capacities are shared across facility labels and service types. Floating accessibility indices, fractional planning units and conservative integer projections have distinct meanings. No global assignment optimality, live traffic or site-selection claim.
