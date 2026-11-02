"""Private-fixture browser login helper; production never imports this file."""
from .accounts import COOKIE

def login_fixture(portal,token):
    account=portal.accounts.register(dict(username='fixture-user',password='fixture-password-123',name='测试用户'))
    key=portal.accounts.issue(account['id'])
    portal.accounts.claim(account,token)
    return COOKIE+'='+key

def claim_fixture(portal,cookie,token):
    account=portal.accounts.resolve(cookie.split('=',1)[1])
    return portal.accounts.claim(account,token)
