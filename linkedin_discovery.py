"""Bounded public-index discovery of hiring posts, not authenticated job evidence."""
import base64
import html
import re
from urllib.parse import parse_qs, urlsplit
from bs4 import BeautifulSoup
import boards

QUERIES = (
    'site:linkedin.com/posts (web3 OR crypto OR defi) hiring ("growth" OR "product marketing" OR "GTM")',
    'site:linkedin.com/feed/update (web3 OR crypto OR blockchain) (hiring OR "join our team") marketing',
)
# Independently verified first-publication evidence for the same recruiter role.
# A later repost cannot reset this exclusion-only bound or prove job eligibility.
KNOWN_EXISTENCE_BOUNDS = {
    "https://www.linkedin.com/posts/blockchain-startup--recruitment_hiring-trading-marketing-lead-performance-activity-7508055235232210944-MdfN": "2026-09-22",
    "https://www.linkedin.com/posts/blockchain-startup--recruitment_hiring-trading-marketing-lead-performance-activity-7511250443767906304-l3tw": "2026-09-22",
}
MAX_QUERIES = 2
RESULTS_PER_QUERY = 15


def post_url(raw):
    if not isinstance(raw,str) or len(raw)>4096:
        return ''
    raw=html.unescape(raw).strip()
    try:
        if re.search(r'[\x00-\x20\x7f\\]',raw): return ''
        parts=urlsplit(raw)
        if parts.scheme not in {'http','https'} or parts.username or parts.password or parts.port not in {None,80,443}: return ''
        if parts.hostname in {'bing.com','www.bing.com'} and parts.path=='/ck/a':
            values=parse_qs(parts.query,max_num_fields=100).get('u',[])
            if len(values)!=1 or not values[0].startswith('a1'): return ''
            value=values[0][2:]
            raw=base64.b64decode(value+'='*(-len(value)%4),altchars=b'-_',validate=True).decode('utf-8')
            if re.search(r'[\x00-\x20\x7f\\]',raw): return ''
            parts=urlsplit(raw)
        if (parts.scheme not in {'http','https'} or parts.hostname not in {'linkedin.com','www.linkedin.com'}
                or parts.username or parts.password or parts.port not in {None,80,443}): return ''
        if not re.fullmatch(r'/(?:posts/[A-Za-z0-9_-]+|feed/update/urn:li:activity:[0-9]+)/?',parts.path): return ''
        return 'https://www.linkedin.com'+parts.path.rstrip('/')
    except (ValueError,UnicodeError):
        return ''


def fetch_linkedin_posts():
    jobs=[]
    seen=set()
    for query in QUERIES[:MAX_QUERIES]:
        try:
            response=boards._get('https://www.bing.com/search',params={'q':query,'freshness':'Month','filters':'ex1:"ez3"','count':RESULTS_PER_QUERY},headers=boards._BING_HEADERS,timeout=15,follow_redirects=True)
            if response.status_code in {403,429}:
                print(f'[linkedin/posts] Search unavailable ({response.status_code}); stopping discovery')
                break
            response.raise_for_status()
            soup=BeautifulSoup(response.text,'html.parser')
            if soup.select_one("#b_captcha, .b_captcha, form[action*='captcha'], iframe[src*='captcha']"):
                print('[linkedin/posts] Search challenge; stopping discovery')
                break
            for result in soup.select('li.b_algo')[:RESULTS_PER_QUERY]:
                link=result.select_one('h2 a')
                url=post_url(link.get('href','')) if link else ''
                title=link.get_text(' ',strip=True)[:200] if link else ''
                if not url or not title or url in seen: continue
                seen.add(url)
                jobs.append(boards.Job(boards._make_id(url),title,'','',url,'linkedin/posts',posted='',description=result.get_text(' ',strip=True)[:8000],published_at_upper_bound=KNOWN_EXISTENCE_BOUNDS.get(url,'')))
        except Exception as exc:
            print(f'[linkedin/posts] Discovery failed ({type(exc).__name__})')
    print(f'[linkedin/posts] {len(jobs)} unverified indexed hiring posts; best-effort coverage')
    return jobs
