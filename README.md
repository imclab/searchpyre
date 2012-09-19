SearchPyre
=======
A simple python Search Engine built on Redis

### Usage
```python
from searchpyre import Pyre, SearchIndex
demigods = SearchIndex()
demigods.index('fire: zuko')
demigods.index_autocomplete('fire: leo vasquez')

pyre = Pyre()
pyre.search('fire')
pyre.autocomplete('zu')

# Django
from searchpyre import SearchModelIndex
SearchModelIndex('app.Model').index('field', 'field2')
SearchModelIndex('app.Model2').index_autocomplete('field', 'field2')

pyre = Pyre()
pyre.search('percy')
pyre.autocomplete('jacks')

```

### Features
* Phonetic Double Metaphone search
* Autocomplete
* Django Model support
