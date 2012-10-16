SearchPyre
=======
A simple python Search Engine built on Redis

### Usage
```python
# Django
from searchpyre import DjangoSearchIndex
DjangoSearchIndex(Model).index('field', 'field2')
DjangoSearchIndex(Model2).index_autocomplete('field', 'field2')

pyre = Pyre()
pyre.search('percy')
pyre.autocomplete('jacks')

```

### Features
* Phonetic Double Metaphone search
* Autocomplete
* Django Model support

### MIT License
[opensource.org/licenses/MIT](http://opensource.org/licenses/MIT)
