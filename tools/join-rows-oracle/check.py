"""tools/join-rows-oracle/check.py EXPECT OUT... -- each host's answers against
the model's rows; OUT files are batch outputs named after the host."""
import sys, collections
expect = open(sys.argv[1]).read().split('\n')[:-1]
outs = {p.rsplit('/', 1)[-1]: open(p).read().split('\n')[:-1] for p in sys.argv[2:]}
verbose = '-v' in sys.argv
wrong = collections.Counter()
pattern = collections.Counter()
for i, want in enumerate(expect):
    bad = tuple(sorted(h for h, o in outs.items() if o[i] != want))
    for h in bad:
        wrong[h] += 1
    if bad:
        pattern[bad] += 1
print(f'{len(expect)} programs; wrong per host: ' + ', '.join(f'{h} {wrong[h]}' for h in outs))
for k, v in pattern.most_common(8):
    print(f'  {v:5}  wrong: {"+".join(k)}')
