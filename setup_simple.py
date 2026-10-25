"""One-time local credentials wizard. Does not change daily strategy parameters."""
import getpass
import os
from pathlib import Path
from simple_agent.data import load_env

ROOT=Path(__file__).resolve().parent

def main():
    target=ROOT/'.env';load_env(target)
    original=target.read_text(encoding='utf-8-sig') if target.exists() else ''
    additions=[]
    fields=[('TEAM_ID','Official team ID',False),('GOOGLE_API_KEY','Gemini API key',True),
            ('FINMIND_API_TOKEN','FinMind Sponsor token (Enter if using Fugle instead)',True),
            ('FUGLE_API_KEY','Fugle developer/advanced key (Enter if using FinMind)',True),
            ('ESUN_API_TOKEN','Official competition API token',True)]
    for key,label,secret in fields:
        if os.environ.get(key):
            print(key+': already set (value hidden)');continue
        value=(getpass.getpass(label+': ') if secret else input(label+': ')).strip()
        if not value:continue
        if any(c in value for c in '\n\r\"\''):
            raise ValueError('Unsupported characters in credential')
        # New value placed before existing blank placeholders; reader uses first assignment.
        additions.append(key+'='+value)
    if not os.environ.get('GEMINI_MODEL'):
        additions.append('GEMINI_MODEL=gemini-3.5-flash-lite')
    if additions:
        target.write_text('\n'.join(additions)+'\n'+original,encoding='utf-8')
        try:target.chmod(0o600)
        except OSError:pass
    print('Saved local configuration. Never commit .env. Team maintainer must verify submit_adapter.json once.')

if __name__=='__main__':main()
