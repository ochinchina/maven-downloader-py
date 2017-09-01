#!/usr/bin/python

import json
import requests
import shutil
import xml.etree.ElementTree as ET 
import argparse
import os
import traceback

class TextColor:
    RED='\033[0;31m'
    GREEN = '\033[0;32m'
    YELLOW = '\033[1;33m'
    NC='\033[0m'

    @staticmethod
    def red(text):
        return "%s%s%s"%(TextColor.RED,text,TextColor.NC)

    @staticmethod
    def green(text):
        return "%s%s%s"%(TextColor.GREEN,text,TextColor.NC)

    @staticmethod
    def yellow( text ):
        return "%s%s%s"%(TextColor.YELLOW,text,TextColor.NC)

class MavenPomDownloader:
    def __init__( self, base_urls ):
        self._base_urls = base_urls
        self._cache = {}

    def download_pom_file( self, mavenLib ):
        """
        download a maven .pom file maven-metadata.xml
        
        Args:
            mavenLib(MavenLib): the maven library
            with_cache(bool): put the downloaded file to cache if with_cache is True
        Return:
            a dictionary with two fields: 'content' and 'base_url'
        """
        if mavenLib in self._cache:
            return self._cache[mavenLib]

        result = {}

        for base_url in self._base_urls:
            try:
                url = base_url+ '/' + mavenLib.relative_pom_path()
                print( TextColor.green( "Download POM %s" % url ) )
                r = requests.get( url )
                if r.status_code >= 200 and r.status_code < 300:
                    result = {'content': r.text, 'base_url': base_url }
                    break
                print( TextColor.red( "Fail to get the POM file %s" % url ) )
            except Exception as e:
                print(e)
        self._cache[mavenLib] = result
        return result

class MavenLib:
    def __init__( self, groupId, artifactId, version ):
        self.groupId = groupId
        self.artifactId = artifactId
        self.version = version

    def relative_pom_path( self ):
        return self._relative_path( "pom" )

    def relative_jar_path( self ):
        return self._relative_path( "jar" )

    def _relative_path( self, suffix ):
        x = self.groupId.split( "." )
        x.extend( [self.artifactId, self.version, "%s-%s.%s" % (self.artifactId, self.version, suffix ) ] )
        return "/".join( x )

    def __hash__( self ):
        hc = hash( self.groupId ) * 33
        hc += hash(self.artifactId)
        return hc * 33 + hash(self.version)

    def __eq__( self, other ):
        return (self.groupId == other.groupId
            and self.artifactId == other.artifactId
            and self.version == other.version )

    def __ne__( self, other ):
        return (self.groupId != other.groupId or
            self.artifactId != other.artifactId or
            self.version != other.version )

    def __cmp__( self, other ):
        t1 = "%s:%s:%s" % (self.groupId, self.artifactId, self.version )
        t2 = "%s:%s:%s" % (other.groupId, other.artifactId, other.version)

        if t1 > t2: return 1
        if t1 < t2: return -1
        return 0

    def __repr__( self ):
        return "%s:%s:%s" % ( self.groupId, self.artifactId, self.version )



class MavenPom:
    def __init__( self, maven_pom_downloader, mavenLib ):
        """
        construct an POM object
        Args:
            maven_pom_downloader(MavenPomDownloader): instance of MavenFileDownloader
            mavenLib(MavenLib): instance of MavenLib
        """
        self._maven_pom_downloader = maven_pom_downloader
        self._properties = {"project.version": mavenLib.version }
        self._dependencies = []
        self._exclusions = []
        self._lib_base_url = {}
        parents = [mavenLib]
        self._all_poms = []
        #get the the poms
        while len( parents ) > 0:
            parent = parents.pop()
            pom_info = self._download_pom( parent )
            if pom_info:
                self._lib_base_url[parent] = pom_info['base_url']
                root = ET.fromstring(pom_info['content'])
                self._all_poms.append( root )
                xmlns = self._extract_xmlns( root.tag )
                self._extract_properties( root, xmlns )
                parent = self._extract_parent( root, xmlns )
                if parent is not None:
                    parents.append( parent )
        for root in self._all_poms:
            xmlns = self._extract_xmlns( root.tag )
            self._extract_dependency_management( root, xmlns )
        for root in self._all_poms:
            xmlns = self._extract_xmlns( root.tag )
            self._extract_all_dependencies( root, xmlns )
            self._extract_dependency_exclusion( root, xmlns )

    def __nonzero__( self ):
        return len( self._all_poms ) > 0

    def __bool__( self ):
        return self.__nonzero__()

    def get_lib_base_url( self, mavenLib ):
        """
        get the maven base url of specified library

        Args:
            lib(MavenLib): instance of MavenLib

        Returns:
            the url base url of library or empty string
        """
        if mavenLib in self._lib_base_url:
            return self._lib_base_url[mavenLib]
        else:
            return ""
    def get_all_dependencies( self ):
        """
        get all the dependencies of the library specified in the constructor

        Args:
            none

        Returns:
            list of the dependencies library(not include the library itself). Each library in the
            list contains MavenLib object instances
        """
        result = []
        for dep in self._dependencies:
            dep['groupId'] = self._eval_with_properties( dep['groupId'] )
            dep['artifactId'] = self._eval_with_properties( dep['artifactId'] )
            if 'version' not in dep:
                version = self._get_version_from_dependency_management( dep['groupId'], dep['artifactId']  )
            else:
                version = self._eval_with_properties( dep['version'] )
            if version:
                groupId = self._eval_with_properties( dep['groupId'] )
                artifactId = self._eval_with_properties( dep['artifactId'] )
                result.append( MavenLib( groupId, artifactId, version ) )
        return result 

    def _get_version_from_dependency_management( self, groupId, artifactId ):
        for dep in self._dependency_management:
            if 'version' in dep and groupId == self._eval_with_properties( dep['groupId'] ) and artifactId == self._eval_with_properties( dep['artifactId'] ):
                return self._eval_with_properties( dep['version'] )
        return ""

    def _extract_xmlns( self, element_name ):
        if element_name.startswith( '{' ):
            return element_name[0: element_name.find( '}' ) + 1]
        else:
            return ""

    def _download_pom( self, mavenLib ):
        """
        Download the pom file of maven library

        Args:
            mavenLib(MavenLib): the maven library

        Returns:
            a dictionay with two keys:
            - conent, is the content of the pom
            - base_url, which URL the pom is downloaded from
        """
        return self._maven_pom_downloader.download_pom_file( mavenLib )

    def _extract_all_dependencies( self, root, xmlns ):
        deps_elem = root.find( '%sdependencies' % xmlns )
        if deps_elem is not None:
            for dep_elem in deps_elem.iter( "%sdependency" % xmlns ):
                dep = self._create_dependency( dep_elem, xmlns )
                if dep:
                    self._dependencies.append( dep )

    def _extract_dependency_management( self, root, xmlns ):
        self._dependency_management = []
        dpm = root.find( '%sdependencyManagement' % xmlns )
        if dpm is not None:
            for dep_elem in dpm.iter( "%sdependency" % xmlns ):
                dep = self._create_dependency( dep_elem, xmlns )
                if dep:
                    self._dependency_management.append( dep )

    def _extract_dependency_exclusion( self, root, xmlns ):
        deps_elem = root.find( '%sdependencies' % xmlns )
        if deps_elem is not None:
            exclusions_elem = deps_elem.iter( '%sexclusion' % xmlns )
            if exclusions_elem is not None:
                for exclusion_elem in exclusions_elem:
                    self._create_dependency( exclusion_elem, xmlns )

    def _create_dependency( self, dep_elem, xmlns ):
        groupId = dep_elem.find( "%sgroupId" % xmlns )
        artifactId = dep_elem.find( '%sartifactId' % xmlns )
        scope = dep_elem.find( '%sscope' % xmlns )
        version = dep_elem.find( '%sversion' % xmlns )
        optional = dep_elem.find( '%soptional' % xmlns )
        if optional is not None and optional.text.lower() in ( "t", "true", "y", "yes", "1" ):
            return None
        if scope is not None and ( scope.text.lower() == "compile" or scope.text.lower() == "test" ):
            return None
        if groupId is not None and artifactId is not None:
            dep = {'groupId': self._eval_with_properties( groupId.text ), 'artifactId': self._eval_with_properties( artifactId.text ) }
            if scope is not None:
                dep['scope'] = scope.text
            if version is not None:
                dep['version'] = self._eval_with_properties( version.text )
            if self._dependency_exist( dep ):
                return None
            return dep
        return None

    def _dependency_exist( self, dep ):
        for d in self._dependencies:
            if d['groupId'] == dep['groupId'] and d['artifactId'] == dep['artifactId']:
                if d['version'] == dep['version']:
                    return True
                print( TextColor.yellow("Warning: different version %s and %s for same library %s:%s" % ( d['version'], dep['version'], d['groupId'], d['artifactId'] ) ) )
                return False
        return False
    def _extract_parent( self, root, xmlns ):
        parent_elem = root.find( '%sparent' % xmlns )
        if parent_elem is not None:
            groupId = parent_elem.find( '%sgroupId' % xmlns )
            artifactId = parent_elem.find( '%sartifactId' % xmlns )
            version = parent_elem.find( '%sversion' % xmlns )
            if groupId is not None and artifactId is not None and version is not None:
                return MavenLib( groupId.text, artifactId.text, version.text )
        return None

    def _extract_properties( self, root, xmlns ):
        properties_elem = root.find( '%sproperties' % xmlns )
        if properties_elem is None:
            return
        for prop_elem in properties_elem:
            name = self._extract_element_name_without_namespace( prop_elem.tag )
            if prop_elem.text is not None:
                self._properties[name] = prop_elem.text

    def _extract_element_name_without_namespace( self, element_tag ):
        if element_tag.startswith( '{' ):
            return element_tag[ element_tag.find( '}' ) + 1:]
        else:
            return element_tag


    def _eval_with_properties( self, val ):
        if val.startswith( '${' ) and val.endswith( '}' ):
            prop_name = val[2:-1]
            if prop_name in self._properties:
                return self._eval_with_properties( self._properties[prop_name] )
            else:
                return ""
        else:
            return val

class MavenLibraryDownloader:
    def __init__( self, maven_pom_downloader, out_dir = "." ):
        self._maven_pom_downloader = maven_pom_downloader
        self._out_dir = out_dir
        self._downloaded_libraries = []

    def download( self, mavenLib ):
        """
        download the maven library from the repostory

        Args:
            mavenLib(MavenLib): the library want to download

        Returns:
           None 
        """
        pom = MavenPom( self._maven_pom_downloader, mavenLib )
        if pom:
            deps = pom.get_all_dependencies()
            base_url = pom.get_lib_base_url( mavenLib )
            if base_url:
                self._do_download( base_url, mavenLib )
            self._downloaded_libraries.append( mavenLib )
            for dep in deps:
                if dep not in self._downloaded_libraries:
                    self.download( dep )

    def _do_download( self, base_url, mavenLib ):
        jar_path = mavenLib.relative_jar_path()
        file_name = os.path.basename( jar_path )
        url = "/".join( [base_url, jar_path ] )
        try:
            r = requests.get(url, stream=True)
            print TextColor.green( "Download %s from %s and save to %s/%s" % ( file_name, url, self._out_dir, file_name ) )
            with open(self._out_dir + '/' + file_name, 'wb') as f:
                shutil.copyfileobj( r.raw, f )
        except Exception as e:
            pass

        
def parse_arg():
    parser = argparse.ArgumentParser()
    parser.add_argument( '--maven_urls', required=False, help='the maven http/https url seperated in comma', default='http://repo1.maven.org/maven2' )
    parser.add_argument( '--libraries', required = True, help = 'the library in format: groupId:artifactId:version[,groupId:artifactId:version]')
    parser.add_argument( '--output', '-o', required=False, help = 'the library output directory', default=".")
    return parser.parse_args()

def parse_maven_base_urls( maven_base_urls ):
    """
    parse the maven urls seperated with comma

    Args:
        maven_base_urls: urls seperated with comma
    Returns:
        a list contains the base url seperated with comma
    """
    result = []
    count = 0
    for base_url in maven_base_urls.split("," ):
        count += 1
        if not is_url_reachable( base_url ):
            continue
        if base_url.endswith( '/' ):
            base_url = base_url[0:-1]
        result.append( base_url )

    if len( result ) <= 0:
        if count > 1:
            print TextColor.yellow( "No any url in %s is reachable" % maven_base_urls )
        else:
            print TestColor.red( "%s is not reachable" % maven_base_urls )
    return result

def parse_downlod_libaries( libraries ):
    """
    parse the libraries separated with comma. And each librarys must be in "groupId:artifactId:version" format

    Args:
        libraries(string): the libraries separated with comma
    Returns:
        a list contains MavenLib object
    """
    result = []
    for lib in libraries.split( "," ):
        try:
            (groupId, artifactId, version ) = lib.split( ":" )
            result.append( MavenLib(groupId, artifactId, version) )
        except:
            print TextColor.red( 'the library %s is not in groupId:artifactId:version format' % lib )
    return result

def is_url_reachable( url ):
    """
    check if the url is reachable or not

    Args:
        url(string): the http/https url

    Returns:
        True if the url is reachable
    """
    try:
        requests.get( url )
        return True
    except Exception as ex:
        print( TextColor.red( traceback.format_exc() ) )
        return False

def make_output_dir( out_dir ):
    """
    try to make output directory

    Args:
        out_dir(string): the absolute or relative output directory

    Returns:
        True if succeed to make output directory, False if fail to make the output directory
    """
    if os.path.exists( out_dir ) and not os.path.isdir( out_dir ):
        print TextColor.red("the output %s is not a directory" % out_dir )
        return False
    if os.path.exists( out_dir ):
        return True
    else:
        try:
            os.makedirs( out_dir )
            return True
        except Exception as ex:
            print( ex )
            return False
def main():
    args = parse_arg()
    out_dir = args.output
    if out_dir.endswith( '/' ): out_dir = out_dir[0:-1]
    download_libraries = parse_downlod_libaries( args.libraries )
    if not download_libraries:
        print TextColor.yellow( "no library is specified for download")
        return

    if not make_output_dir( args.output ):
        print TextColor.red("fail to make directory %s" % args.output )
        return

    maven_base_urls = parse_maven_base_urls(args.maven_urls)
    if not maven_base_urls:
        return

    file_downloader = MavenPomDownloader( maven_base_urls )
    for lib in download_libraries:
        try:
            lib_downloader = MavenLibraryDownloader( file_downloader, out_dir )
            lib_downloader.download( lib )
        except Exception as ex:
            print ex


if __name__ == "__main__":
    main()
