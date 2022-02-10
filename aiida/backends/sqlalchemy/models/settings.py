# -*- coding: utf-8 -*-
###########################################################################
# Copyright (c), The AiiDA team. All rights reserved.                     #
# This file is part of the AiiDA code.                                    #
#                                                                         #
# The code is hosted on GitHub at https://github.com/aiidateam/aiida-core #
# For further information on the license, see the LICENSE.txt file        #
# For further information please visit http://www.aiida.net               #
###########################################################################
# pylint: disable=import-error,no-name-in-module
"""Module to manage node settings for the SQLA backend."""
from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.schema import UniqueConstraint
from sqlalchemy.sql.schema import Index
from sqlalchemy.types import DateTime, Integer, String, Text

from aiida.backends.sqlalchemy.models.base import Base
from aiida.common import timezone


class DbSetting(Base):
    """Database model to store global settings."""
    __tablename__ = 'db_dbsetting'

    id = Column(Integer, primary_key=True)  # pylint: disable=invalid-name

    key = Column(String(1024), nullable=False)
    val = Column(JSONB, default={})

    # I also add a description field for the variables
    description = Column(Text, default='', nullable=False)
    time = Column(DateTime(timezone=True), default=timezone.now, onupdate=timezone.now, nullable=False)

    __table_args__ = (
        # index/constraint names mirror django's auto-generated ones
        UniqueConstraint(key, name='db_dbsetting_key_1b84beb4_uniq'),
        Index(
            'db_dbsetting_key_1b84beb4_like',
            key,
            postgresql_using='btree',
            postgresql_ops={'key': 'varchar_pattern_ops'}
        ),
    )

    def __str__(self):
        return f"'{self.key}'={self.val}"
