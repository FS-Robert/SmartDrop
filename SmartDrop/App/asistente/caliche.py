"""Caliche salvadoreño: palabras y dichos de El Salvador para que Gotita los entienda y hable cercano.

Gotita entiende todo el glosario. Al hablar solo usa las expresiones amables de USAR (en español y en modo
sencillo), y nunca las de SOLO_ENTENDER (groserías), porque pueden ofender aunque la persona las diga.
"""

# Palabras del agua y la casa: son las que más se oyen al hablar de SmartDrop.
AGUA = (
    ('chorro', 'grifo o llave del agua ("abrí el chorro", "el chorro gotea")'),
    ('pila', 'depósito de cemento donde se guarda el agua y se lava ropa o trastes'),
    ('barril / tambo', 'recipiente grande donde se guarda agua'),
    ('cántaro', 'recipiente para acarrear o guardar agua'),
    ('pipa', 'camión cisterna que reparte agua'),
    ('contador', 'medidor del agua de la casa'),
    ('ANDA', 'la empresa pública de agua de El Salvador'),
    ('no ha caído el agua / se fue el agua', 'no hay servicio de agua en ese momento'),
    ('el agua viene bajita / viene sin fuerza', 'hay poca presión'),
    ('se rebalsó', 'se llenó hasta derramarse'),
    ('el tubo está pacho / está pashte', 'el tubo está aplastado o tapado'),
    ('guacal', 'recipiente (como una palangana o tazón) para sacar agua'),
)

GENERAL = (
    ('chero / chera / alero', 'amigo o amiga de confianza'),
    ('cipote / cipota / bicho / bicha', 'niño, niña o joven'),
    ('chichí', 'bebé'),
    ('chivo / qué chivo / chivísimo', 'algo bueno, bonito o que está bien'),
    ('está yuca', 'está difícil o complicado'),
    ('pisto', 'dinero'),
    ('andar hule / andar pelado', 'no tener dinero'),
    ('de choto', 'gratis'),
    ('chamba', 'trabajo'),
    ('chunche / bolado / volado', 'cosa, objeto o asunto (palabra comodín)'),
    ('tiliches', 'cosas, trastes o pertenencias'),
    ('chumpa', 'chaqueta'),
    ('caite', 'sandalia'),
    ('chucho', 'perro (también se le dice al tacaño)'),
    ('chele / chela', 'persona de piel o pelo claro'),
    ('cabal', 'exacto, así es'),
    ('simón / sí va', 'sí'),
    ('nel / nambe', 'no; también sorpresa ("nambe, ¿en serio?")'),
    ('vaya / va pues / dale', 'de acuerdo, está bien'),
    ('fijate / fíjese', 'mira, presta atención'),
    ('púchica / púchica vos', 'expresión de sorpresa o molestia, sin ser grosera'),
    ('ala gran', 'expresión de sorpresa'),
    ('ni modo', 'ya no se puede hacer nada'),
    ('ahorita / dendioy', 'ahora mismo, o dentro de un rato'),
    ('ya merito', 'casi, falta poco'),
    ('a todo mecate', 'muy rápido'),
    ('un cachimbo / un vergo', 'mucho, una gran cantidad'),
    ('salado', 'con mala suerte'),
    ('torcido', 'mala suerte o un problema'),
    ('chambre / chambrear', 'chisme / chismear'),
    ('hablar paja', 'decir mentiras o hablar sin sentido'),
    ('agüevado', 'aburrido o desanimado'),
    ('encachimbado', 'muy enojado'),
    ('goma', 'malestar después de tomar alcohol'),
    ('bolo', 'borracho'),
    ('guaro', 'licor'),
    ('chompipe', 'pavo'),
    ('pupusa', 'comida típica salvadoreña'),
    ('guanaco', 'salvadoreño (dicho con cariño)'),
    ('chotear', 'vigilar, quedarse viendo'),
    ('chiche', 'fácil'),
    ('chuco', 'sucio'),
    ('zompopo', 'hormiga grande'),
    ('pepenar', 'recoger algo del suelo'),
    ('jalar', 'ser novios'),
    ('mara', 'grupo de gente o de amigos'),
    ('cuilio / la tira', 'la policía'),
    ('va de nuez', 'otra vez'),
    ('qué onda / qué ondas', 'saludo: ¿qué tal?'),
)

# Expresiones amables que Gotita puede usar (pocas, sin exagerar).
USAR = ('vaya', 'va pues', 'dale', 'cabal', 'fíjate', 'fíjese', 'qué chivo', 'todo chivo', 'ni modo',
        'ya merito', 'púchica')

# Groserías y palabras fuertes: Gotita entiende lo que la persona quiere decir (casi siempre que algo no
# funciona o que está molesta), pero nunca las dice ni las repite.
SOLO_ENTENDER = (
    ('mierda', 'algo malo, roto o que no sirve ("esta mierda no sirve" = esto no funciona); también enojo'),
    ('a la mierda / vete a la mierda', 'enojo fuerte o mandar lejos'),
    ('verga', 'según el tono: enojo ("¡a la verga!"), algo muy bueno ("de a verga", "vergón") o mucho ("un vergo de gente")'),
    ('hecho verga / vergueado', 'arruinado, quebrado o muy mal'),
    ('vergazo / turcazo / cachimbazo', 'golpe fuerte; también "un montón"'),
    ('puta / puta madre / hijueputa / hijuepucha', 'enojo o sorpresa fuerte; no siempre va contra alguien'),
    ('cerote', 'según el tono: amigo ("qué onda, cerote"), persona mala o una cosa cualquiera'),
    ('maje', 'amigo, o tonto según el tono'),
    ('pisado / este pisado', 'una persona o cosa que fastidia ("el pisado contador no marca")'),
    ('jodido / se jodió / joder', 'arruinado, se descompuso o molestar ("ya se jodió la bomba")'),
    ('cagada / está cagado / qué cagada', 'algo malo o un error ("qué cagada, no hay agua")'),
    ('encachimbado / encabronado / emputado', 'muy enojado'),
    ('chingado / chingadera', 'algo que fastidia o no sirve'),
    ('mamada / babosada', 'tontería, algo sin sentido'),
    ('pendejo / culero / cabrón', 'insultos; la persona está molesta'),
    ('baboso / bayunco / dundo', 'tonto'),
    ('bolo', 'borracho'),
    ('pijudo / pijazo', 'muy bueno / golpe fuerte'),
    ('chucho (como tacaño)', 'tacaño'),
    ('jaña', 'novia'),
    ('bicho', 'niño (fuera de El Salvador puede ofender)'),
)


def glosario_para_gotita():
    """Texto del glosario para las instrucciones de Gotita."""
    lineas = ['Palabras del agua y la casa:']
    lineas += [f'- {palabra}: {significado}' for palabra, significado in AGUA]
    lineas.append('Otras palabras y dichos:')
    lineas += [f'- {palabra}: {significado}' for palabra, significado in GENERAL]
    lineas.append('Expresiones amables que puedes usar: ' + ', '.join(USAR) + '.')
    lineas.append('Groserías y palabras fuertes: las entiendes por lo que la persona quiere decir, pero nunca las dices '
                  'ni las repites:')
    lineas += [f'- {palabra}: {significado}' for palabra, significado in SOLO_ENTENDER]
    return '\n'.join(lineas)
