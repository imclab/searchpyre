SearchPyre
=======

#### Python Search Engine

A simple python Search Engine built on Redis

### Usage
```python
from searchpyre import Pyre, SearchIndex
benders = SearchIndex('benders')
benders.add('fire: leo vasquez')
benders.add('fire: zuko', autocompletion=True)

pyre = Pyre()
pyre.search('fire')
pyre.autocomplete('zu')
```

### Features
* Phonetic Double Metaphone search
* Autocomplete
* Query constraints
